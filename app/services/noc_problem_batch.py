from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.settings import Settings, get_settings
from app.services.checkmk_operational import collect_checkmk_operational_snapshot
from app.services.noc_autonomy_control import request_selected_run
from app.services.noc_skills import load_noc_skills, select_noc_skill


def _problem_key(item: dict[str, Any]) -> str:
    return str(item.get("problem_key") or "").strip()


def _site_id(item: dict[str, Any]) -> str:
    return str(item.get("site_id") or item.get("site") or "").strip()


def _host(item: dict[str, Any]) -> str:
    return str(item.get("host") or "").strip()


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
    """Tira uma fotografia nova do Checkmk e retorna os grupos por procedure."""

    settings = settings or get_settings()
    snapshot = collect_checkmk_operational_snapshot(settings=settings)
    status = str(snapshot.get("status") or "")
    if status != "completed":
        return {
            "status": status or "failed",
            "groups": [],
            "error": snapshot.get("error"),
            "sites_ok": int(snapshot.get("sites_ok") or 0),
            "sites_failed": int(snapshot.get("sites_failed") or 0),
        }
    problems = [dict(item) for item in snapshot.get("problems") or [] if isinstance(item, dict)]
    groups = group_problems_by_procedure(problems)
    return {
        "status": "completed",
        "master_skill_id": "noc-master",
        "problem_count": len(problems),
        "group_count": len(groups),
        "groups": groups,
        "sites_ok": int(snapshot.get("sites_ok") or 0),
        "sites_failed": int(snapshot.get("sites_failed") or 0),
        "hosts_seen": int(snapshot.get("hosts_seen") or 0),
    }


def request_procedure_batch(
    procedure_id: str,
    *,
    sites: list[str] | None = None,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Enfileira todos os alertas atuais que ainda pertencem ao procedure pedido.

    A seleção é recalculada no instante do clique. Isso impede executar um lote
    com problem_keys obsoletos vindos de uma fotografia anterior da interface.
    O runner utilizado é o mesmo da correção manual existente.
    """

    settings = settings or get_settings()
    normalized = str(procedure_id or "").strip()
    known = {skill.id: skill for skill in load_noc_skills()}
    if normalized not in known:
        raise ValueError(f"procedure inexistente na NOC Master Skill: {normalized}")

    snapshot = collect_checkmk_operational_snapshot(settings=settings)
    if str(snapshot.get("status") or "") != "completed":
        raise RuntimeError(
            f"não foi possível obter uma fotografia atual do Checkmk: {snapshot.get('status') or 'failed'}"
        )

    site_filter = {str(item).strip() for item in sites or [] if str(item).strip()}
    matched: list[dict[str, Any]] = []
    for raw in snapshot.get("problems") or []:
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
        },
    }
