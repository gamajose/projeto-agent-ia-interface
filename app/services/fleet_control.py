from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.fleet_models import FleetAssetORM, FleetDiscoveryRunORM
from app.services.fleet_discovery import discovery_networks, fleet_discovery_status, fleet_discovery_tick


_CONTROL_LOCK = threading.Lock()
_CONTROL_THREAD: threading.Thread | None = None


def _thread_running() -> bool:
    return _CONTROL_THREAD is not None and _CONTROL_THREAD.is_alive()


def _active_run() -> FleetDiscoveryRunORM | None:
    ensure_database_schema()
    with SessionLocal() as session:
        row = session.scalar(
            select(FleetDiscoveryRunORM)
            .where(FleetDiscoveryRunORM.status.in_(["pending", "running"]))
            .order_by(FleetDiscoveryRunORM.started_at.desc())
            .limit(1)
        )
        if row:
            session.expunge(row)
        return row


def _latest_run() -> FleetDiscoveryRunORM | None:
    ensure_database_schema()
    with SessionLocal() as session:
        row = session.scalar(
            select(FleetDiscoveryRunORM).order_by(FleetDiscoveryRunORM.started_at.desc()).limit(1)
        )
        if row:
            session.expunge(row)
        return row


def _usable_count(network) -> int:
    if network.prefixlen <= 30:
        return max(0, int(network.num_addresses) - 2)
    return int(network.num_addresses)


def _create_manual_run(settings: Settings) -> FleetDiscoveryRunORM:
    networks = discovery_networks(settings)
    latest = _latest_run()
    trigger = "manual_rediscovery" if latest else "manual_initial"
    with SessionLocal() as session:
        active = session.scalar(
            select(FleetDiscoveryRunORM)
            .where(FleetDiscoveryRunORM.status.in_(["pending", "running"]))
            .order_by(FleetDiscoveryRunORM.started_at.desc())
            .limit(1)
        )
        if active:
            session.expunge(active)
            return active
        row = FleetDiscoveryRunORM(
            trigger=trigger,
            status="running",
            cidrs=[str(item) for item in networks],
            total_candidates=sum(_usable_count(item) for item in networks),
            metadata_payload={
                "strategy": "full_private_cidr_sweep",
                "read_only_fingerprint": True,
                "started_by": "operator",
            },
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row


def _discovery_loop(settings: Settings) -> None:
    while True:
        try:
            result = fleet_discovery_tick(settings=settings)
            status = str(result.get("status") or "")
            if status == "running":
                time.sleep(1)
                continue
            if status == "busy":
                time.sleep(5)
                continue
            return
        except Exception:
            time.sleep(10)


def _launch(settings: Settings) -> bool:
    global _CONTROL_THREAD
    with _CONTROL_LOCK:
        if _thread_running():
            return True
        thread = threading.Thread(
            target=_discovery_loop,
            args=(settings,),
            name="fleet-discovery-manual",
            daemon=True,
        )
        thread.start()
        _CONTROL_THREAD = thread
        return True


def start_fleet_discovery(*, settings: Settings | None = None) -> dict[str, Any]:
    """Inicia uma descoberta completa somente após ação explícita do operador."""
    settings = settings or get_settings()
    ensure_database_schema()
    run = _active_run() or _create_manual_run(settings)
    _launch(settings)
    return {
        "started": True,
        "resumed": run.trigger not in {"manual_initial", "manual_rediscovery"},
        "run_id": str(run.id),
        "trigger": run.trigger,
        "status": fleet_control_status(settings=settings),
    }


def resume_active_fleet_discovery(*, settings: Settings | None = None) -> bool:
    """Retoma somente uma descoberta que já havia sido iniciada antes do restart."""
    settings = settings or get_settings()
    if _active_run() is None:
        return False
    return _launch(settings)


def has_active_fleet_discovery() -> bool:
    return _active_run() is not None


def _age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((now - value).total_seconds()))


def fleet_control_status(
    *,
    settings: Settings | None = None,
    mapped_limit: int = 100,
    not_accessed_limit: int = 100,
) -> dict[str, Any]:
    settings = settings or get_settings()
    base = fleet_discovery_status(limit_unreachable=not_accessed_limit)
    run = dict(base.get("run") or {})
    total = int(run.get("total_candidates") or 0)
    scanned = int(run.get("scanned") or 0)
    progress = round((scanned / total) * 100, 2) if total else 0.0

    updated_at = None
    if run.get("updated_at"):
        try:
            updated_at = datetime.fromisoformat(str(run["updated_at"]))
        except ValueError:
            updated_at = None
    heartbeat_age = _age_seconds(updated_at)
    active = str(run.get("status") or "") in {"pending", "running"}
    stalled = bool(active and heartbeat_age is not None and heartbeat_age > 180)

    with SessionLocal() as session:
        rows = session.scalars(
            select(FleetAssetORM)
            .order_by(FleetAssetORM.last_checked_at.desc())
            .limit(max(1, min(int(mapped_limit), 300)))
        ).all()
        mapped = [
            {
                "name": row.client_name or row.hostname or row.address,
                "client_name": row.client_name,
                "hostname": row.hostname,
                "address": row.address,
                "ssh_port": row.ssh_port,
                "access_status": row.access_status,
                "environment": row.environment,
                "roles": list(row.roles or []),
                "capabilities": list(row.capabilities or []),
                "monitoring_detected": bool(row.monitoring_detected),
                "monitoring_confidence": int(row.monitoring_confidence or 0),
                "checkmk_sites": list(row.checkmk_sites or []),
                "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
            }
            for row in rows
        ]

    if not run:
        phase = "not_started"
    elif active:
        phase = "running"
    elif str(run.get("status") or "") == "completed":
        phase = "inventory_ready"
    else:
        phase = str(run.get("status") or "unknown")

    return {
        **base,
        "phase": phase,
        "progress_percent": progress,
        "controller_running": _thread_running(),
        "heartbeat_age_seconds": heartbeat_age,
        "stalled": stalled,
        "mapped": mapped,
        "operational_mode": (
            "discovery_required"
            if phase == "not_started"
            else "building_inventory"
            if phase == "running"
            else "inventory_ready"
            if phase == "inventory_ready"
            else phase
        ),
    }
