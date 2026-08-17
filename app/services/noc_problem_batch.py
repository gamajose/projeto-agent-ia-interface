from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from redis import Redis
from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkProblemORM
from app.services.checkmk_operational import (
    checkmk_operational_overview,
    collect_checkmk_operational_snapshot,
)
from app.services.noc_autonomy_control import get_selected_run, request_selected_run
from app.services.noc_skills import load_noc_skills, select_noc_skill


_RUN_TTL_SECONDS = 7200
_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled"}


def _problem_key(item: dict[str, Any]) -> str:
    return str(item.get("problem_key") or "").strip()


def _site_id(item: dict[str, Any]) -> str:
    return str(item.get("site_id") or item.get("site") or "").strip()


def _host(item: dict[str, Any]) -> str:
    return str(item.get("host") or "").strip()


def _client_alias(item: dict[str, Any]) -> str:
    return str(item.get("client_alias") or item.get("alias") or _site_id(item) or "").strip()


def _event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "site_id": _site_id(item),
        "host": _host(item),
        "host_address": item.get("host_address"),
        "service": item.get("service"),
        "state_name": item.get("state_name") or item.get("state"),
        "output": item.get("output") or item.get("plugin_output"),
        "host_kind": item.get("host_kind"),
    }


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _prefix(settings: Settings) -> str:
    return str(getattr(settings, "noc_incident_prefix", "agent-ia:noc") or "agent-ia:noc").rstrip(":")


def _run_key(settings: Settings, run_id: str) -> str:
    return f"{_prefix(settings)}:autonomy:run:{run_id}"


def _active_batch_key(settings: Settings, procedure_id: str) -> str:
    return f"{_prefix(settings)}:batch:active:{procedure_id}"


def _persisted_active_problems() -> list[dict[str, Any]]:
    """Lê a última fotografia concluída persistida no PostgreSQL."""

    ensure_database_schema()
    with SessionLocal() as session:
        rows = session.scalars(
            select(CheckmkProblemORM)
            .where(CheckmkProblemORM.active.is_(True))
            .order_by(CheckmkProblemORM.state.desc(), CheckmkProblemORM.last_seen_at.desc())
        ).all()
        return [
            {
                "problem_key": row.problem_key,
                "site_id": row.site_id,
                "client_alias": row.client_alias,
                "alias": row.client_alias,
                "kind": row.kind,
                "host": row.host_name,
                "host_address": row.internal_address,
                "service": row.service,
                "state": row.state,
                "state_name": row.state_name,
                "output": row.output,
                "skill_id": row.skill_id,
                "skill_title": row.skill_title,
                "route_strategy": row.route_strategy,
                "automation_status": row.automation_status,
                "incident_id": row.incident_id,
                "job_id": row.job_id,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "host_kind": str((row.metadata_payload or {}).get("host_kind") or "") or None,
            }
            for row in rows
        ]


def _operation_meta() -> dict[str, Any]:
    overview = checkmk_operational_overview(problem_limit=1, site_limit=1)
    operation = dict(overview.get("state") or {})
    return {
        "status": "completed",
        "source": "persisted",
        "busy": bool(operation.get("running")),
        "completed_at": operation.get("last_completed_at"),
        "sites_ok": int(operation.get("sites_ok") or 0),
        "sites_failed": int(operation.get("sites_failed") or 0),
        "hosts_seen": int(operation.get("hosts_seen") or 0),
    }


def _problems_for_batch(
    *,
    settings: Settings,
    wait_for_busy: bool,
    refresh: bool,
    busy_timeout_seconds: float = 12.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Obtém a fotografia usada pelo lote.

    Abrir o modal usa a fotografia persistida e é instantâneo. Uma atualização
    explícita ou o clique em Arrumar todos ainda executa uma fotografia nova.
    Se outra ronda já estiver em andamento, o GET usa a última fotografia e a
    execução aguarda a ronda concorrente concluir por alguns segundos.
    """

    if not refresh:
        problems = _persisted_active_problems()
        meta = _operation_meta()
        if problems or meta.get("completed_at"):
            if meta.get("busy"):
                meta["source"] = "persisted_while_busy"
                meta["warning"] = "coleta do Checkmk em andamento; exibindo a última fotografia concluída"
            return problems, meta
        refresh = True

    snapshot = collect_checkmk_operational_snapshot(settings=settings)
    status = str(snapshot.get("status") or "failed")
    if status == "completed":
        return (
            [dict(item) for item in snapshot.get("problems") or [] if isinstance(item, dict)],
            {
                "status": "completed",
                "source": "live",
                "busy": False,
                "started_at": snapshot.get("started_at"),
                "completed_at": snapshot.get("completed_at"),
                "sites_ok": int(snapshot.get("sites_ok") or 0),
                "sites_failed": int(snapshot.get("sites_failed") or 0),
                "hosts_seen": int(snapshot.get("hosts_seen") or 0),
            },
        )

    if status != "busy":
        return [], {
            "status": status,
            "source": "live",
            "busy": False,
            "error": snapshot.get("error"),
            "sites_ok": int(snapshot.get("sites_ok") or 0),
            "sites_failed": int(snapshot.get("sites_failed") or 0),
            "hosts_seen": int(snapshot.get("hosts_seen") or 0),
        }

    if wait_for_busy:
        deadline = time.monotonic() + max(0.5, float(busy_timeout_seconds))
        while time.monotonic() < deadline:
            meta = _operation_meta()
            if not bool(meta.get("busy")):
                problems = _persisted_active_problems()
                meta.update(
                    {
                        "source": "persisted_after_busy",
                        "busy": False,
                        "warning": "uma ronda do Checkmk já estava em andamento; o lote usou a fotografia recém-concluída",
                    }
                )
                return problems, meta
            time.sleep(0.25)
        raise RuntimeError(
            "o Checkmk continua ocupado com outra ronda; aguarde a coleta atual terminar e tente novamente"
        )

    problems = _persisted_active_problems()
    meta = _operation_meta()
    meta.update(
        {
            "source": "persisted_while_busy",
            "busy": True,
            "warning": "coleta do Checkmk em andamento; exibindo a última fotografia concluída",
        }
    )
    return problems, meta


def group_problems_by_procedure(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa a fotografia atual de alertas pelo procedure da NOC Master Skill."""

    catalog = {skill.id: skill.as_dict() for skill in load_noc_skills()}
    grouped: dict[str, dict[str, Any]] = {}
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in problems:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = _problem_key(item)
        if not key:
            continue
        selected = select_noc_skill(_event(item), host_kind=str(item.get("host_kind") or "") or None)
        procedure_id = str(selected.get("procedure_id") or selected.get("id") or "generic-checkmk-alert")
        members[procedure_id].append(item)
        if procedure_id not in grouped:
            procedure = catalog.get(procedure_id, selected)
            grouped[procedure_id] = {
                "master_skill_id": "noc-master",
                "procedure_id": procedure_id,
                "title": str(procedure.get("title") or procedure_id),
                "playbook_id": procedure.get("playbook_id"),
                "target_strategy": procedure.get("target_strategy"),
            }

    result: list[dict[str, Any]] = []
    for procedure_id, base in grouped.items():
        items = members[procedure_id]
        sites = sorted({_site_id(item) for item in items if _site_id(item)})
        hosts = sorted({_host(item) for item in items if _host(item)})
        services = sorted(
            {str(item.get("service") or "").strip() for item in items if str(item.get("service") or "").strip()}
        )
        result.append(
            {
                **base,
                "problem_count": len(items),
                "host_count": len(hosts),
                "site_count": len(sites),
                "sites": sites,
                "hosts": hosts,
                "services": services,
                "problem_keys": [_problem_key(item) for item in items],
                "sample": [
                    {
                        "problem_key": _problem_key(item),
                        "site_id": _site_id(item),
                        "client_alias": _client_alias(item),
                        "host": _host(item),
                        "host_address": item.get("host_address"),
                        "service": item.get("service"),
                        "state": item.get("state_name") or item.get("state"),
                        "output": str(item.get("output") or item.get("plugin_output") or "")[:300],
                    }
                    for item in items[:5]
                ],
            }
        )

    return sorted(result, key=lambda item: (-int(item.get("problem_count") or 0), str(item.get("title") or "")))


def current_problem_groups(*, refresh: bool = False, settings: Settings | None = None) -> dict[str, Any]:
    """Lista grupos sem nova ronda por padrão; refresh=True força uma fotografia."""

    settings = settings or get_settings()
    problems, meta = _problems_for_batch(settings=settings, wait_for_busy=False, refresh=bool(refresh))
    if str(meta.get("status") or "") != "completed":
        return {
            "status": str(meta.get("status") or "failed"),
            "groups": [],
            "error": meta.get("error"),
            "sites_ok": int(meta.get("sites_ok") or 0),
            "sites_failed": int(meta.get("sites_failed") or 0),
        }
    groups = group_problems_by_procedure(problems)
    return {
        "status": "completed",
        "source": meta.get("source"),
        "busy": bool(meta.get("busy")),
        "warning": meta.get("warning"),
        "completed_at": meta.get("completed_at"),
        "master_skill_id": "noc-master",
        "problem_count": len(problems),
        "group_count": len(groups),
        "groups": groups,
        "sites_ok": int(meta.get("sites_ok") or 0),
        "sites_failed": int(meta.get("sites_failed") or 0),
        "hosts_seen": int(meta.get("hosts_seen") or 0),
    }


def problem_group_detail(procedure_id: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Lista empresa/site, host e alertas do procedure a partir do último snapshot persistido."""

    settings = settings or get_settings()
    normalized = str(procedure_id or "").strip()
    known = {skill.id: skill for skill in load_noc_skills()}
    if normalized not in known and normalized != "generic-checkmk-alert":
        raise ValueError(f"procedure inexistente na NOC Master Skill: {normalized}")

    matched: list[dict[str, Any]] = []
    for item in _persisted_active_problems():
        selected = select_noc_skill(_event(item), host_kind=str(item.get("host_kind") or "") or None)
        selected_id = str(selected.get("procedure_id") or selected.get("id") or "")
        if selected_id == normalized and _problem_key(item):
            matched.append(item)

    by_host: dict[tuple[str, str], dict[str, Any]] = {}
    for item in matched:
        site_id = _site_id(item)
        host = _host(item)
        key = (site_id, host)
        member = by_host.setdefault(
            key,
            {
                "site_id": site_id,
                "client_alias": _client_alias(item),
                "host": host,
                "host_address": item.get("host_address"),
                "alert_count": 0,
                "alerts": [],
            },
        )
        member["alert_count"] = int(member.get("alert_count") or 0) + 1
        member["alerts"].append(
            {
                "problem_key": _problem_key(item),
                "service": item.get("service"),
                "state": item.get("state_name") or item.get("state"),
                "output": str(item.get("output") or item.get("plugin_output") or "")[:500],
                "automation_status": item.get("automation_status"),
            }
        )

    members = sorted(
        by_host.values(),
        key=lambda item: (
            str(item.get("client_alias") or item.get("site_id") or "").casefold(),
            str(item.get("host") or "").casefold(),
        ),
    )
    operation = _operation_meta()
    skill = known.get(normalized)
    return {
        "status": "completed",
        "master_skill_id": "noc-master",
        "procedure_id": normalized,
        "title": skill.title if skill else "Investigação genérica de alerta Checkmk",
        "problem_count": len(matched),
        "host_count": len(members),
        "site_count": len({_site_id(item) for item in matched if _site_id(item)}),
        "last_completed_at": operation.get("completed_at"),
        "snapshot_running": bool(operation.get("busy")),
        "members": members,
    }


def _active_batch_run(procedure_id: str, *, settings: Settings) -> dict[str, Any] | None:
    client = _redis(settings)
    key = _active_batch_key(settings, procedure_id)
    run_id = str(client.get(key) or "").strip()
    if not run_id:
        return None
    run = get_selected_run(run_id, settings=settings)
    if not run or str(run.get("status") or "") in _TERMINAL_RUN_STATES:
        client.delete(key)
        return None
    return run


def _save_batch_context(
    run: dict[str, Any],
    *,
    procedure_id: str,
    batch: dict[str, Any],
    snapshot_completed_at: Any,
    settings: Settings,
) -> dict[str, Any]:
    payload = dict(run)
    scope = dict(payload.get("scope") or {})
    scope.update(
        {
            "batch_source": "problem_batch",
            "batch_procedure_id": procedure_id,
            "batch_snapshot_completed_at": snapshot_completed_at,
        }
    )
    payload["scope"] = scope
    payload["batch"] = dict(batch)
    client = _redis(settings)
    client.setex(
        _run_key(settings, str(payload.get("id") or "")),
        _RUN_TTL_SECONDS,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    client.setex(
        _active_batch_key(settings, procedure_id),
        _RUN_TTL_SECONDS,
        str(payload.get("id") or ""),
    )
    return payload


def request_procedure_batch(
    procedure_id: str,
    *,
    sites: list[str] | None = None,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Atualiza a fotografia uma vez e enfileira o procedure escolhido.

    A mesma execução é reaproveitada enquanto ainda estiver queued/running, o que
    impede dois cliques de criarem correções concorrentes para o mesmo procedure.
    O runner recebe o timestamp da fotografia recém-concluída para não repetir uma
    segunda coleta global imediatamente depois.
    """

    settings = settings or get_settings()
    normalized = str(procedure_id or "").strip()
    known = {skill.id: skill for skill in load_noc_skills()}
    if normalized not in known:
        raise ValueError(f"procedure inexistente na NOC Master Skill: {normalized}")

    active = _active_batch_run(normalized, settings=settings)
    if active:
        batch = dict(active.get("batch") or {})
        batch["reused"] = True
        return {**active, "batch": batch}

    problems, meta = _problems_for_batch(settings=settings, wait_for_busy=True, refresh=True)
    if str(meta.get("status") or "") != "completed":
        raise RuntimeError(
            f"não foi possível obter uma fotografia atual do Checkmk: {meta.get('status') or 'failed'}"
        )

    site_filter = {str(item).strip() for item in sites or [] if str(item).strip()}
    matched: list[dict[str, Any]] = []
    for raw in problems:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if site_filter and _site_id(item) not in site_filter:
            continue
        selected = select_noc_skill(_event(item), host_kind=str(item.get("host_kind") or "") or None)
        selected_id = str(selected.get("procedure_id") or selected.get("id") or "")
        if selected_id == normalized and _problem_key(item):
            matched.append(item)

    if not matched:
        raise ValueError(f"nenhum problema ativo corresponde ao procedure {normalized}")

    problem_keys = list(dict.fromkeys(_problem_key(item) for item in matched if _problem_key(item)))
    run = request_selected_run(
        sites=sorted(site_filter) if site_filter else None,
        problem_keys=problem_keys,
        skill_id=normalized,
        operator=operator,
        settings=settings,
    )
    skill = known[normalized]
    batch = {
        "master_skill_id": "noc-master",
        "procedure_id": normalized,
        "title": skill.title,
        "problem_count": len(problem_keys),
        "host_count": len({_host(item) for item in matched if _host(item)}),
        "site_count": len({_site_id(item) for item in matched if _site_id(item)}),
        "problem_keys": problem_keys,
        "snapshot_source": meta.get("source"),
        "snapshot_completed_at": meta.get("completed_at"),
        "reused": False,
    }
    return _save_batch_context(
        run,
        procedure_id=normalized,
        batch=batch,
        snapshot_completed_at=meta.get("completed_at"),
        settings=settings,
    )
