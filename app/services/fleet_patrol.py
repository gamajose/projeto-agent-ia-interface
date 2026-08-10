from __future__ import annotations

import re
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.fleet_models import FleetAssetORM, FleetDiscoveryRunORM
from app.db.models import HostORM
from app.services.fleet_control import fleet_control_status
from app.services.jobs import enqueue_investigation
from app.services.noc_incidents import attach_job, incident_objective, register_checkmk_event
from app.services.noc_targeting import resolve_noc_target
from app.services.persistence import upsert_mapping
from app.services.redaction import redact_text
from app.services.runner import ResolvedTarget, build_executor
from app.services.runtime_env import runtime_bool, runtime_int


_PATROL_LOCK = threading.Lock()
_PATROL_THREAD: threading.Thread | None = None
_PATROL_STATE: dict[str, Any] = {
    "running": False,
    "cycle": 0,
    "last_started_at": None,
    "last_completed_at": None,
    "last_error": None,
    "monitors_checked": 0,
    "problems_seen": 0,
    "new_incidents": 0,
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def _config(settings: Settings) -> dict[str, Any]:
    return {
        "enabled": runtime_bool("FLEET_PATROL_ENABLED", True, settings=settings),
        "interval": runtime_int("FLEET_PATROL_INTERVAL_SECONDS", 300, minimum=30, maximum=86400, settings=settings),
        "concurrency": runtime_int("FLEET_PATROL_CONCURRENCY", 4, minimum=1, maximum=16, settings=settings),
        "command_timeout": runtime_int("FLEET_PATROL_COMMAND_TIMEOUT_SECONDS", 45, minimum=10, maximum=300, settings=settings),
        "max_monitors": runtime_int("FLEET_PATROL_MAX_MONITORS", 500, minimum=1, maximum=5000, settings=settings),
    }


def _patrol_query() -> str:
    service_query = (
        "GET services\\n"
        "Columns: host_name host_address description state last_check plugin_output\\n"
        "Filter: state >= 1"
    )
    host_query = (
        "GET hosts\\n"
        "Columns: name address state last_check plugin_output\\n"
        "Filter: state >= 1"
    )
    sq = shlex.quote(service_query)
    hq = shlex.quote(host_query)
    return (
        "if command -v omd >/dev/null 2>&1; then "
        "for s in $(omd sites --bare 2>/dev/null); do "
        "printf 'PATROL_CONTEXT|local|%s\\n' \"$s\"; "
        f"su - \"$s\" -c \"lq {sq}\" 2>/dev/null | sed 's/^/PATROL_SERVICE|/'; "
        f"su - \"$s\" -c \"lq {hq}\" 2>/dev/null | sed 's/^/PATROL_HOST|/'; "
        "done; fi; "
        "if command -v docker >/dev/null 2>&1; then "
        "for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -Ei 'checkmk|check-mk'); do "
        "for s in $(docker exec \"$c\" omd sites --bare 2>/dev/null); do "
        "printf 'PATROL_CONTEXT|%s|%s\\n' \"$c\" \"$s\"; "
        f"docker exec \"$c\" su - \"$s\" -c \"lq {sq}\" 2>/dev/null | sed 's/^/PATROL_SERVICE|/'; "
        f"docker exec \"$c\" su - \"$s\" -c \"lq {hq}\" 2>/dev/null | sed 's/^/PATROL_HOST|/'; "
        "done; done; fi"
    )


def _parse_patrol_output(output: str) -> list[dict[str, Any]]:
    container: str | None = None
    site: str | None = None
    items: list[dict[str, Any]] = []
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if line.startswith("PATROL_CONTEXT|"):
            parts = line.split("|", 2)
            container = parts[1] if len(parts) > 1 else None
            site = parts[2] if len(parts) > 2 else None
            continue
        if line.startswith("PATROL_SERVICE|"):
            parts = line[len("PATROL_SERVICE|") :].split(";", 5)
            if len(parts) < 5:
                continue
            try:
                state = int(parts[3])
            except ValueError:
                continue
            items.append(
                {
                    "kind": "service",
                    "container": container,
                    "site": site,
                    "host": parts[0].strip(),
                    "host_address": parts[1].strip(),
                    "service": parts[2].strip(),
                    "state": state,
                    "last_check": parts[4].strip(),
                    "output": parts[5].strip() if len(parts) > 5 else "",
                }
            )
            continue
        if line.startswith("PATROL_HOST|"):
            parts = line[len("PATROL_HOST|") :].split(";", 4)
            if len(parts) < 4:
                continue
            try:
                state = int(parts[2])
            except ValueError:
                continue
            items.append(
                {
                    "kind": "host",
                    "container": container,
                    "site": site,
                    "host": parts[0].strip(),
                    "host_address": parts[1].strip(),
                    "service": "Host status",
                    "state": state,
                    "last_check": parts[3].strip(),
                    "output": parts[4].strip() if len(parts) > 4 else "",
                }
            )
    return [item for item in items if item.get("host") and item.get("service")]


def _monitor_assets(limit: int) -> list[FleetAssetORM]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(FleetAssetORM)
            .where(FleetAssetORM.monitoring_detected.is_(True), FleetAssetORM.access_status == "ok")
            .order_by(FleetAssetORM.client_name.asc(), FleetAssetORM.address.asc())
            .limit(limit)
        ).all()
        for row in rows:
            session.expunge(row)
        return rows


def _find_affected_host(host_name: str, host_address: str) -> HostORM | None:
    with SessionLocal() as session:
        asset = None
        if host_name:
            asset = session.scalar(
                select(FleetAssetORM)
                .where(
                    or_(
                        FleetAssetORM.hostname.ilike(host_name),
                        FleetAssetORM.client_name.ilike(host_name),
                    )
                )
                .order_by(FleetAssetORM.last_accessible_at.desc())
                .limit(1)
            )
        if asset and asset.inventory_host_id:
            row = session.get(HostORM, asset.inventory_host_id)
            if row:
                session.expunge(row)
                return row
        if host_address:
            row = session.scalar(
                select(HostORM)
                .where(HostORM.internal_ips.contains([host_address]))
                .order_by(HostORM.last_seen_at.desc())
                .limit(1)
            )
            if row:
                session.expunge(row)
                return row
    return None


def _monitor_host(asset: FleetAssetORM) -> HostORM | None:
    if not asset.inventory_host_id:
        return None
    with SessionLocal() as session:
        row = session.get(HostORM, asset.inventory_host_id)
        if row:
            session.expunge(row)
        return row


def _state_name(state: int) -> str:
    return {1: "WARN", 2: "CRIT", 3: "UNKNOWN"}.get(int(state), "CRIT")


def _event_key(item: dict[str, Any]) -> str:
    return f"{item.get('site') or ''}|{item.get('host') or ''}|{item.get('service') or ''}"


def _previous_patrol(asset_id) -> dict[str, dict[str, Any]]:
    with SessionLocal() as session:
        row = session.get(FleetAssetORM, asset_id)
        evidence = dict(row.evidence or {}) if row else {}
    result: dict[str, dict[str, Any]] = {}
    for item in list(evidence.get("patrol_active") or []):
        key = _event_key(dict(item))
        if key:
            result[key] = dict(item)
    return result


def _save_patrol_state(asset_id, *, current: list[dict[str, Any]], error: str | None = None) -> None:
    with SessionLocal() as session:
        row = session.get(FleetAssetORM, asset_id)
        if not row:
            return
        evidence = dict(row.evidence or {})
        evidence["patrol_active"] = [
            {
                "site": item.get("site"),
                "host": item.get("host"),
                "host_address": item.get("host_address"),
                "service": item.get("service"),
                "state": item.get("state"),
            }
            for item in current
        ]
        evidence["patrol_last_checked_at"] = datetime.now(timezone.utc).isoformat()
        evidence["patrol_problem_count"] = len(current)
        evidence["patrol_last_error"] = error
        row.evidence = evidence
        session.commit()


def _register_recovery(item: dict[str, Any], settings: Settings) -> None:
    try:
        register_checkmk_event(
            host=str(item.get("host") or ""),
            service=str(item.get("service") or ""),
            state="OK",
            output="Recuperação observada pela ronda automática do Fleet Controller.",
            site=str(item.get("site") or "") or None,
            environment=EnvironmentType.UNKNOWN.value,
            requested_auto_correct=False,
            settings=settings,
        )
    except Exception:
        return


def _register_problem(asset: FleetAssetORM, item: dict[str, Any], settings: Settings) -> dict[str, Any]:
    affected = _find_affected_host(str(item.get("host") or ""), str(item.get("host_address") or ""))
    monitor = _monitor_host(asset)
    if affected and monitor:
        try:
            upsert_mapping(
                affected_host_id=affected.id,
                monitoring_host_id=monitor.id,
                same_server=affected.id == monitor.id,
                container_name=str(item.get("container") or "") or None,
                site_name=str(item.get("site") or "") or None,
                checkmk_hostname=str(item.get("host") or "") or None,
                checkmk_version=None,
            )
        except Exception:
            pass

    environment = str(affected.environment if affected else EnvironmentType.UNKNOWN.value)
    event = register_checkmk_event(
        host=str(item.get("host") or ""),
        service=str(item.get("service") or ""),
        state=_state_name(int(item.get("state") or 2)),
        output=str(item.get("output") or "")[:12000],
        site=str(item.get("site") or "") or None,
        environment=environment,
        requested_auto_correct=False,
        settings=settings,
    )
    incident = dict(event.get("incident") or {})
    if not event.get("should_investigate", True) or not incident or not settings.noc_auto_investigate:
        return {"new": False, "event": event}

    if affected:
        routing = resolve_noc_target(
            checkmk_host=str(item.get("host") or ""),
            service=str(item.get("service") or ""),
            output=str(item.get("output") or ""),
            explicit_target=None,
            requested_environment=EnvironmentType(environment),
        )
        target = str(routing.get("reference") or affected.vpn_ip)
        port = int(routing.get("ssh_port") or affected.ssh_port or 22)
        try:
            target_environment = EnvironmentType(str(routing.get("environment") or environment))
        except ValueError:
            target_environment = EnvironmentType.UNKNOWN
    else:
        routing = {
            "reference": asset.address,
            "ssh_port": asset.ssh_port,
            "environment": EnvironmentType.UNKNOWN.value,
            "source": "fleet_patrol_monitor_fallback",
        }
        target = asset.address
        port = int(asset.ssh_port or 22)
        target_environment = EnvironmentType.UNKNOWN

    queued = enqueue_investigation(
        target,
        incident_objective(incident),
        environment=target_environment,
        mode="propose",
        approve=False,
        ssh_port=port,
        metadata={
            "source": "fleet_patrol",
            "noc_incident_id": incident.get("id"),
            "site": item.get("site"),
            "service": item.get("service"),
            "state": _state_name(int(item.get("state") or 2)),
            "checkmk_host": item.get("host"),
            "checkmk_address": item.get("host_address"),
            "monitor_asset": asset.address,
            "noc_routing": routing,
        },
        settings=settings,
    )
    if incident.get("id"):
        attach_job(str(incident["id"]), str(queued["job_id"]), settings=settings)
    return {"new": True, "event": event, "job": queued}


def _probe_monitor(asset: FleetAssetORM, settings: Settings) -> dict[str, Any]:
    cfg = _config(settings)
    target = ResolvedTarget(
        reference=asset.address,
        host=asset.address,
        port=int(asset.ssh_port or 22),
        environment=EnvironmentType.UNKNOWN,
        inventory=None,
    )
    executor = build_executor(target, settings=settings)
    started = time.monotonic()
    try:
        executor.connect()
        result = executor.run_sudo(
            _patrol_query(),
            EnvironmentType.UNKNOWN,
            approved=False,
            timeout=int(cfg["command_timeout"]),
        )
        items = _parse_patrol_output(result.stdout)
        return {
            "asset": asset,
            "ok": True,
            "items": items,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "asset": asset,
            "ok": False,
            "items": [],
            "error": redact_text(f"{type(exc).__name__}: {exc}")[:2000],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        executor.close()


def fleet_patrol_cycle(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    ensure_database_schema()
    cfg = _config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    discovery = fleet_control_status(settings=settings, mapped_limit=1, not_accessed_limit=1)
    if discovery.get("phase") != "inventory_ready":
        return {"status": "waiting_for_discovery", "phase": discovery.get("phase")}
    if not _PATROL_LOCK.acquire(blocking=False):
        return {"status": "busy"}

    started_at = datetime.now(timezone.utc)
    _PATROL_STATE.update(
        {
            "running": True,
            "cycle": int(_PATROL_STATE.get("cycle") or 0) + 1,
            "last_started_at": started_at.isoformat(),
            "last_error": None,
            "monitors_checked": 0,
            "problems_seen": 0,
            "new_incidents": 0,
        }
    )
    try:
        assets = _monitor_assets(int(cfg["max_monitors"]))
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=int(cfg["concurrency"]), thread_name_prefix="fleet-patrol") as pool:
            futures = {pool.submit(_probe_monitor, asset, settings): asset.address for asset in assets}
            for future in as_completed(futures):
                results.append(future.result())

        new_incidents = 0
        problems = 0
        for result in results:
            asset: FleetAssetORM = result["asset"]
            previous = _previous_patrol(asset.id)
            if not result.get("ok"):
                _save_patrol_state(asset.id, current=list(previous.values()), error=str(result.get("error") or "erro de acesso"))
                continue
            current_items = list(result.get("items") or [])
            current = {_event_key(item): item for item in current_items}
            for key, old in previous.items():
                if key not in current:
                    _register_recovery(old, settings)
            for item in current_items:
                problems += 1
                try:
                    registered = _register_problem(asset, item, settings)
                    if registered.get("new"):
                        new_incidents += 1
                except Exception:
                    continue
            _save_patrol_state(asset.id, current=current_items, error=None)

        completed_at = datetime.now(timezone.utc)
        _PATROL_STATE.update(
            {
                "running": False,
                "last_completed_at": completed_at.isoformat(),
                "monitors_checked": len(results),
                "problems_seen": problems,
                "new_incidents": new_incidents,
            }
        )
        return {"status": "completed", **fleet_patrol_status()}
    except Exception as exc:
        _PATROL_STATE.update({"running": False, "last_error": f"{type(exc).__name__}: {exc}"})
        return {"status": "error", **fleet_patrol_status()}
    finally:
        _PATROL_LOCK.release()


def fleet_patrol_status() -> dict[str, Any]:
    return dict(_PATROL_STATE)


def _loop(settings: Settings) -> None:
    cfg = _config(settings)
    while True:
        result = fleet_patrol_cycle(settings=settings)
        status = str(result.get("status") or "")
        if status == "waiting_for_discovery":
            time.sleep(30)
        elif status == "busy":
            time.sleep(10)
        else:
            time.sleep(int(cfg["interval"]))


def start_fleet_patrol_background(*, settings: Settings | None = None) -> bool:
    global _PATROL_THREAD
    settings = settings or get_settings()
    if not _config(settings)["enabled"]:
        return False
    if _PATROL_THREAD is not None and _PATROL_THREAD.is_alive():
        return True
    thread = threading.Thread(target=_loop, args=(settings,), name="fleet-patrol", daemon=True)
    thread.start()
    _PATROL_THREAD = thread
    return True
