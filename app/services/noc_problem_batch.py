from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkProblemORM
from app.services.checkmk_operational import (
    checkmk_operational_overview,
    collect_checkmk_operational_snapshot,
)
from app.services.noc_autonomy_control import request_selected_run
from app.services.noc_skills import load_noc_skills, select_noc_skill


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


def _persisted_active_problems() -> list[dict[str, Any]]:
    """Lê a última fotografia concluída persistida no PostgreSQL.

    O coletor do Checkmk usa um lock não bloqueante. Quando outra ronda já está
    em andamento, a UI pode continuar exibindo esta fotografia sem iniciar uma
    segunda coleta concorrente nem transformar ``busy`` em erro operacional.
    """

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


def _problems_for_batch(
    *,
    settings: Settings,
    wait_for_busy: bool,
    busy_timeout_seconds: float = 12.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Obtém problemas atuais sem tratar uma coleta concorrente como falha.

    GETs podem usar imediatamente a última fotografia persistida se outra ronda
    estiver executando. Para uma correção em lote, esperamos a ronda concorrente
    terminar e só então usamos o estado que ela acabou de persistir.
    """

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
            overview = checkmk_operational_overview(problem_limit=1, site_limit=1)
            operation = dict(overview.get("state") or {})
            if not bool(operation.get("running")):
                problems = _persisted_active_problems()
                return problems, {
                    "status": "completed",
                    "source": "persisted_after_busy",
                    "busy": False,
                    "warning": "uma ronda do Checkmk já estava em andamento; o lote usou a fotografia recém-concluída",
                    "completed_at": operation.get("last_completed_at"),
                    "sites_ok": int(operation.get("sites_ok") or 0),
                    "sites_failed": int(operation.get("sites_failed") or 0),
                    "hosts_seen": int(operation.get("hosts_seen") or 0),
                }
            time.sleep(0.25)
        raise RuntimeError(
            "o Checkmk continua ocupado com outra ronda; aguarde alguns segundos e tente novamente"
        )

    problems = _persisted_active_problems()
    overview = checkmk_operational_overview(problem_limit=1, site_limit=1)
    operation = dict(overview.get("state") or {})
    return problems, {
        "status": "completed",
        "source": "persisted_while_busy",
        "busy": True,
        "warning": "coleta do Checkmk em andamento; exibindo a última fotografia concluída",
        "completed_at": operation.get("last_completed_at"),
        "sites_ok": int(operation.get("sites_ok") or 0),
        "sites_failed": int(operation.get("sites_failed") or 0),
        "hosts_seen": int(operation.get("hosts_seen") or 0),
    }


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
        services = sorted({str(item.get("service") or "").strip() for item in items if str(item.get("service") or "").strip()})
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


def current_problem_groups(*, settings: Settings | None = None) -> dict[str, Any]:
    """Tira uma fotografia nova ou usa a última concluída quando o coletor está ocupado."""

    settings = settings or get_settings()
    problems, meta = _problems_for_batch(settings=settings, wait_for_busy=False)
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
    """Lista empresa/site, host e alertas que pertencem a um procedure.

    O detalhe é deliberadamente lido da última fotografia persistida para não
    disparar uma segunda ronda apenas porque o operador abriu o modal.
    """

    settings = settings or get_settings()
    normalized = str(procedure_id or "").strip()
    known = {skill.id: skill for skill in load_noc_skills()}
    if normalized not in known:
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
    overview = checkmk_operational_overview(problem_limit=1, site_limit=1)
    operation = dict(overview.get("state") or {})
    skill = known[normalized]
    return {
        "status": "completed",
        "master_skill_id": "noc-master",
        "procedure_id": normalized,
        "title": skill.title,
        "problem_count": len(matched),
        "host_count": len(members),
        "site_count": len({_site_id(item) for item in matched if _site_id(item)}),
        "last_completed_at": operation.get("last_completed_at"),
        "snapshot_running": bool(operation.get("running")),
        "members": members,
    }


def request_procedure_batch(
    procedure_id: str,
    *,
    sites: list[str] | None = None,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Enfileira todos os alertas atuais que ainda pertencem ao procedure pedido.

    A seleção é recalculada no instante do clique. Se uma ronda do Checkmk já
    estiver em andamento, esperamos ela terminar e usamos a fotografia que ela
    acabou de persistir, em vez de falhar com ``busy``.
    """

    settings = settings or get_settings()
    normalized = str(procedure_id or "").strip()
    known = {skill.id: skill for skill in load_noc_skills()}
    if normalized not in known:
        raise ValueError(f"procedure inexistente na NOC Master Skill: {normalized}")

    problems, meta = _problems_for_batch(settings=settings, wait_for_busy=True)
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

    problem_keys = [_problem_key(item) for item in matched]
    run = request_selected_run(
        sites=sorted(site_filter) if site_filter else None,
        problem_keys=problem_keys,
        skill_id=normalized,
        operator=operator,
        settings=settings,
    )
    skill = known[normalized]
    return {
        **run,
        "batch": {
            "master_skill_id": "noc-master",
            "procedure_id": normalized,
            "title": skill.title,
            "problem_count": len(matched),
            "host_count": len({_host(item) for item in matched if _host(item)}),
            "site_count": len({_site_id(item) for item in matched if _site_id(item)}),
            "problem_keys": problem_keys,
            "snapshot_source": meta.get("source"),
            "snapshot_completed_at": meta.get("completed_at"),
        },
    }
