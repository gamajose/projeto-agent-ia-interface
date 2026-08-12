from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.checkmk_master import (
    checkmk_master_status,
    poll_checkmk_master_problems,
    sync_checkmk_master_inventory,
)
from app.services.checkmk_site_targeting import resolve_checkmk_site_target
from app.services.jobs import enqueue_investigation
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
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config(settings: Settings) -> dict[str, Any]:
    return {
        "enabled": runtime_bool("CHECKMK_MASTER_PATROL_ENABLED", True, settings=settings),
        "poll_interval": runtime_int(
            "CHECKMK_MASTER_POLL_INTERVAL_SECONDS", 120, minimum=30, maximum=3600, settings=settings
        ),
        "inventory_sync_hours": runtime_int(
            "CHECKMK_MASTER_INVENTORY_SYNC_HOURS", 6, minimum=1, maximum=168, settings=settings
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
    except Exception:
        return


def _register_problem(item: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
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
    if not incident or not event.get("should_investigate", True) or not settings.noc_auto_investigate:
        return {"new": False, "queued": False, "event": event, "route": route}
    if not route.get("valid") or not route.get("auto_investigate"):
        return {"new": bool(event.get("created")), "queued": False, "event": event, "route": route}

    skill = dict(route.get("skill") or {})
    objective = build_skill_objective(
        item,
        skill,
        site_id=str(route.get("site_id") or item.get("site_id") or ""),
        client_alias=str(route.get("client_alias") or item.get("alias") or ""),
    )
    if incident:
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


def checkmk_master_patrol_cycle(*, settings: Settings | None = None, force_sync: bool = False) -> dict[str, Any]:
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
        }
    )
    try:
        master = checkmk_master_status(settings=settings)
        last_sync = master.get("last_sync_at")
        sync_due = force_sync or not int(master.get("sites_total") or 0)
        if last_sync and not sync_due:
            try:
                parsed = datetime.fromisoformat(str(last_sync).replace("Z", "+00:00"))
                sync_due = (_now() - parsed).total_seconds() >= int(cfg["inventory_sync_hours"]) * 3600
            except ValueError:
                sync_due = True
        if sync_due:
            sync = sync_checkmk_master_inventory(settings=settings)
            _PATROL_STATE["last_inventory_sync_at"] = sync.get("completed_at")

        snapshot = poll_checkmk_master_problems(settings=settings)
        problems = list(snapshot.get("problems") or [])
        recoveries = list(snapshot.get("recoveries") or [])
        new_incidents = 0
        jobs = 0
        guarded = 0
        for item in problems:
            try:
                result = _register_problem(item, settings=settings)
                new_incidents += int(bool(result.get("new")))
                jobs += int(bool(result.get("queued")))
                route = dict(result.get("route") or {})
                if route.get("valid") and not route.get("auto_investigate"):
                    guarded += 1
            except Exception as exc:
                _PATROL_STATE["last_error"] = redact_text(f"{type(exc).__name__}: {exc}")[:1800]
        for item in recoveries:
            _register_recovery(item, settings=settings)

        completed = _now()
        _PATROL_STATE.update(
            {
                "last_completed_at": completed.isoformat(),
                "problems_seen": len(problems),
                "recoveries_seen": len(recoveries),
                "new_incidents": new_incidents,
                "jobs_queued": jobs,
                "guarded_sites": guarded,
            }
        )
        return {
            "status": "completed",
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "problems_seen": len(problems),
            "recoveries_seen": len(recoveries),
            "new_incidents": new_incidents,
            "jobs_queued": jobs,
            "guarded_sites": guarded,
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
    return {
        **dict(_PATROL_STATE),
        "enabled": bool(_config(settings)["enabled"]),
        "master": checkmk_master_status(settings=settings),
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
