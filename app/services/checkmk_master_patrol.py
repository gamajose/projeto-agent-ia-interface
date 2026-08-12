from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.checkmk_customer_sync import sync_checkmk_customers_from_inventory
from app.services.checkmk_master import checkmk_master_status
from app.services.checkmk_operational import (
    checkmk_operational_overview,
    collect_checkmk_operational_snapshot,
    update_problem_automation,
)
from app.services.checkmk_site_targeting import resolve_checkmk_site_target
from app.services.jobs import enqueue_investigation
from app.services.noc_action_policy import classify_problem_category, record_history_transition
from app.services.noc_incidents import attach_job, incident_objective, register_checkmk_event
from app.services.noc_skills import build_skill_objective
from app.services.redaction import redact_text
from app.services.runtime_env import runtime_bool, runtime_int


_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()
_PATROL_STATE: dict[str, Any] = {
    "running": False,
    "cycle": 0,
    "last_started_at": None,
    "last_completed_at": None,
    "last_inventory_sync_at": None,
    "last_error": None,
    "problems_seen": 0,
    "recoveries_seen": 0,
    "new_incidents": 0,
    "jobs_queued": 0,
    "guarded_sites": 0,
    "sites_ok": 0,
    "sites_failed": 0,
    "hosts_seen": 0,
    "customer_sync": {},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config(settings: Settings) -> dict[str, Any]:
    return {
        "enabled": runtime_bool("CHECKMK_MASTER_PATROL_ENABLED", True, settings=settings),
        "poll_interval": runtime_int(
            "CHECKMK_MASTER_POLL_INTERVAL_SECONDS", 120, minimum=30, maximum=3600, settings=settings
        ),
    }


def _environment(value: str | None) -> EnvironmentType:
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _register_recovery(item: dict[str, Any], *, settings: Settings) -> None:
    try:
        register_checkmk_event(
            host=str(item.get("host") or ""),
            service=str(item.get("service") or ""),
            state="OK",
            output="Recuperacao observada pelo CMK05/master.",
            site=str(item.get("site_id") or "") or None,
            environment=EnvironmentType.UNKNOWN.value,
            requested_auto_correct=False,
            settings=settings,
        )
        record_history_transition(
            item,
            status="resolved",
            reason="Checkmk confirmou recuperação/normalização do alerta.",
        )
    except Exception:
        return


def _register_problem(item: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
    item = dict(item)
    item["policy_category"] = classify_problem_category(item)
    route = resolve_checkmk_site_target(item)
    environment = str(route.get("environment") or EnvironmentType.UNKNOWN.value)
    event = register_checkmk_event(
        host=str(item.get("host") or ""),
        service=str(item.get("service") or ""),
        state=str(item.get("state_name") or "CRIT"),
        output=str(item.get("output") or "")[:12000],
        site=str(item.get("site_id") or "") or None,
        environment=environment,
        requested_auto_correct=False,
        settings=settings,
    )
    incident = dict(event.get("incident") or {})
    if not incident:
        return {"new": False, "queued": False, "event": event, "route": route}

    if not event.get("should_investigate", True) or not settings.noc_auto_investigate:
        return {"new": False, "queued": False, "event": event, "route": route}
    if not route.get("valid") or not route.get("auto_investigate"):
        return {"new": bool(event.get("action") == "created"), "queued": False, "event": event, "route": route}

    skill = dict(route.get("skill") or {})
    objective = build_skill_objective(
        item,
        skill,
        site_id=str(route.get("site_id") or item.get("site_id") or ""),
        client_alias=str(route.get("client_alias") or item.get("alias") or ""),
    )
    objective = incident_objective(incident) + "\n\n" + objective

    playbook_id = str(skill.get("playbook_id") or "").strip() or None
    queued = enqueue_investigation(
        str(route.get("entry_address") or ""),
        objective,
        environment=_environment(environment),
        mode="propose",
        approve=False,
        ssh_port=22,
        playbook_mode="manual" if playbook_id else "auto",
        playbook_id=playbook_id,
        metadata={
            "source": "checkmk_master",
            "site_scope": True,
            "noc_incident_id": incident.get("id"),
            "checkmk_problem_key": item.get("problem_key"),
            "policy_category": item.get("policy_category"),
            "site_id": route.get("site_id"),
            "client_alias": route.get("client_alias"),
            "entry_address": route.get("entry_address"),
            "livestatus_port": route.get("livestatus_port"),
            "status_host": route.get("status_host"),
            "internal_target": route.get("internal_address"),
            "checkmk_host": item.get("host"),
            "checkmk_address": item.get("host_address"),
            "service": item.get("service"),
            "state": item.get("state_name"),
            "target_strategy": route.get("strategy"),
            "host_kind": route.get("host_kind"),
            "scope_key": route.get("scope_key"),
            "skill": skill,
            "isolation": {
                "site_id": route.get("site_id"),
                "cross_site_internal_ip_lookup": False,
                "reuse_other_customer_session": False,
            },
        },
        settings=settings,
    )
    if incident.get("id"):
        attach_job(str(incident["id"]), str(queued["job_id"]), settings=settings)
    return {"new": True, "queued": True, "event": event, "route": route, "job": queued}


def _persist_automation_result(item: dict[str, Any], result: dict[str, Any]) -> None:
    event = dict(result.get("event") or {})
    incident = dict(event.get("incident") or {})
    route = dict(result.get("route") or {})
    job = dict(result.get("job") or {})
    if result.get("queued"):
        status = "queued"
        reason = "Investigação automática iniciada. A correção dependerá da política da categoria."
    elif route.get("valid") and not route.get("auto_investigate"):
        status = "manual_required"
        reason = str(route.get("reason") or "Rota protegida; exige validação do analista.")
    elif not route.get("valid"):
        status = "access_blocked"
        reason = str(route.get("reason") or "Não foi possível montar uma rota segura para investigação.")
    else:
        status = str(incident.get("status") or "detected")
        reason = "Problema detectado pelo Checkmk e registrado no NOC."
    update_problem_automation(
        str(item.get("problem_key") or ""),
        automation_status="needs_attention" if status in {"manual_required", "access_blocked"} else status,
        incident_id=str(incident.get("id") or "") or None,
        job_id=str(job.get("job_id") or incident.get("job_id") or "") or None,
        route=route,
    )
    record_history_transition(
        item,
        status=status,
        reason=reason,
        incident_id=str(incident.get("id") or "") or None,
        job_id=str(job.get("job_id") or incident.get("job_id") or "") or None,
        metadata={
            "route_valid": route.get("valid"),
            "route_strategy": route.get("strategy"),
            "shared_endpoint": route.get("shared_endpoint"),
        },
    )


def checkmk_master_patrol_cycle(*, settings: Settings | None = None, force_sync: bool = False) -> dict[str, Any]:
    """Executa um ciclo operacional completo.

    Cada ciclo lê os sites do master, coleta hosts e anomalias via Livestatus,
    persiste o inventário, materializa a aba Clientes e entrega cada problema ao
    Incident Manager. ``force_sync`` é mantido por compatibilidade; o snapshot
    já sincroniza tudo.
    """

    settings = settings or get_settings()
    cfg = _config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    if not _THREAD_LOCK.acquire(blocking=False):
        return {"status": "busy"}

    started = _now()
    _PATROL_STATE.update(
        {
            "running": True,
            "cycle": int(_PATROL_STATE.get("cycle") or 0) + 1,
            "last_started_at": started.isoformat(),
            "last_error": None,
            "problems_seen": 0,
            "recoveries_seen": 0,
            "new_incidents": 0,
            "jobs_queued": 0,
            "guarded_sites": 0,
            "sites_ok": 0,
            "sites_failed": 0,
            "hosts_seen": 0,
            "customer_sync": {},
        }
    )
    try:
        snapshot = collect_checkmk_operational_snapshot(settings=settings)
        if snapshot.get("status") != "completed":
            return snapshot

        customer_sync: dict[str, Any]
        try:
            customer_sync = sync_checkmk_customers_from_inventory()
        except Exception as exc:
            customer_sync = {
                "sites_total": 0,
                "sites_synced": 0,
                "sites_failed": 1,
                "errors": [{"error": redact_text(f"{type(exc).__name__}: {exc}")[:1000]}],
            }

        problems = list(snapshot.get("problems") or [])
        recoveries = list(snapshot.get("recoveries") or [])
        new_incidents = 0
        jobs = 0
        guarded = 0
        processing_errors: list[str] = []

        for item in problems:
            try:
                result = _register_problem(item, settings=settings)
                _persist_automation_result(item, result)
                new_incidents += int(bool(result.get("new")))
                jobs += int(bool(result.get("queued")))
                route = dict(result.get("route") or {})
                if route.get("valid") and not route.get("auto_investigate"):
                    guarded += 1
            except Exception as exc:
                message = redact_text(f"{type(exc).__name__}: {exc}")[:600]
                processing_errors.append(message)
                update_problem_automation(
                    str(item.get("problem_key") or ""),
                    automation_status="needs_attention",
                )
                record_history_transition(item, status="failed", reason=message)

        for item in recoveries:
            _register_recovery(item, settings=settings)

        completed = _now()
        site_errors = list(snapshot.get("site_errors") or [])
        last_error = None
        if site_errors:
            last_error = f"{len(site_errors)} site(s) sem resposta Livestatus"
        if processing_errors:
            suffix = f"{len(processing_errors)} problema(s) falharam no roteamento"
            last_error = f"{last_error}; {suffix}" if last_error else suffix
        customer_sync_failures = int(customer_sync.get("sites_failed") or 0) + int(customer_sync.get("host_errors") or 0)
        if customer_sync_failures:
            suffix = f"{customer_sync_failures} falha(s) ao salvar Clientes/hosts"
            last_error = f"{last_error}; {suffix}" if last_error else suffix

        _PATROL_STATE.update(
            {
                "last_completed_at": completed.isoformat(),
                "last_inventory_sync_at": snapshot.get("completed_at"),
                "problems_seen": len(problems),
                "recoveries_seen": len(recoveries),
                "new_incidents": new_incidents,
                "jobs_queued": jobs,
                "guarded_sites": guarded,
                "sites_ok": int(snapshot.get("sites_ok") or 0),
                "sites_failed": int(snapshot.get("sites_failed") or 0),
                "hosts_seen": int(snapshot.get("hosts_seen") or 0),
                "customer_sync": customer_sync,
                "last_error": last_error,
            }
        )
        return {
            "status": "completed",
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "sites_ok": int(snapshot.get("sites_ok") or 0),
            "sites_failed": int(snapshot.get("sites_failed") or 0),
            "hosts_seen": int(snapshot.get("hosts_seen") or 0),
            "problems_seen": len(problems),
            "recoveries_seen": len(recoveries),
            "new_incidents": new_incidents,
            "jobs_queued": jobs,
            "guarded_sites": guarded,
            "site_errors": site_errors,
            "processing_errors": processing_errors,
            "customer_sync": customer_sync,
            "master": checkmk_master_status(settings=settings),
        }
    except Exception as exc:
        message = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
        _PATROL_STATE["last_error"] = message
        return {"status": "failed", "error": message}
    finally:
        _PATROL_STATE["running"] = False
        _THREAD_LOCK.release()


def checkmk_master_patrol_status(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    config = _config(settings)
    return {
        **dict(_PATROL_STATE),
        "enabled": bool(config["enabled"]),
        "poll_interval_seconds": int(config["poll_interval"]),
        "master": checkmk_master_status(settings=settings),
        "operational": checkmk_operational_overview(problem_limit=200, site_limit=500),
    }


def _loop(settings: Settings) -> None:
    cfg = _config(settings)
    while True:
        checkmk_master_patrol_cycle(settings=settings)
        time.sleep(int(cfg["poll_interval"]))


def start_checkmk_master_patrol_background(*, settings: Settings | None = None) -> bool:
    global _THREAD
    settings = settings or get_settings()
    if not _config(settings)["enabled"]:
        return False
    if _THREAD is not None and _THREAD.is_alive():
        return True
    _THREAD = threading.Thread(
        target=_loop,
        args=(settings,),
        name="checkmk-master-patrol",
        daemon=True,
    )
    _THREAD.start()
    return True
