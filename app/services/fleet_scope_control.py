from __future__ import annotations

import ipaddress
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, engine, ensure_database_schema
from app.db.fleet_models import FleetDiscoveryRunORM
from app.services.fleet_control import fleet_control_status
from app.services.fleet_discovery import (
    _DISCOVERY_LOCK,
    _POSTGRES_ADVISORY_LOCK,
    _addresses_for_batch,
    _config,
    _run_dict,
    _upsert_probe_result,
    discovery_networks,
    probe_fleet_host,
)
from app.services.redaction import redact_text


_SCOPE_THREAD: threading.Thread | None = None
_SCOPE_THREAD_LOCK = threading.Lock()


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


def has_active_fleet_discovery() -> bool:
    return _active_run() is not None


def _authorized_networks(settings: Settings) -> list[ipaddress.IPv4Network]:
    return discovery_networks(settings)


def parse_discovery_scope(scope: str | None, *, settings: Settings | None = None) -> list[ipaddress.IPv4Network]:
    """Converte a faixa informada na UI em CIDRs privados dentro da faixa autorizada.

    Exemplos aceitos:
    - ``172.27.*`` / ``172.27.*.*`` -> 172.27.0.0/16
    - ``172.27.1`` / ``172.27.1.*`` -> 172.27.1.0/24
    - ``172.27.1.50`` -> 172.27.1.50/32
    - ``172.27.10.0/24`` -> CIDR informado

    O operador só pode reduzir o universo configurado em FLEET_DISCOVERY_CIDRS;
    a UI nunca amplia a autorização de rede.
    """
    settings = settings or get_settings()
    authorized = _authorized_networks(settings)
    raw = str(scope or "").strip()
    if not raw:
        return authorized

    value = re.sub(r"\s+", "", raw)
    network: ipaddress.IPv4Network

    if "/" in value:
        parsed = ipaddress.ip_network(value, strict=False)
        if not isinstance(parsed, ipaddress.IPv4Network):
            raise ValueError("a descoberta aceita somente IPv4")
        network = parsed
    else:
        tokens = value.split(".")
        while tokens and tokens[-1] == "":
            tokens.pop()
        wildcard_at = next((idx for idx, token in enumerate(tokens) if token == "*"), None)
        if wildcard_at is not None:
            if any(token != "*" for token in tokens[wildcard_at:]):
                raise ValueError("o curinga * só pode aparecer no final da faixa")
            tokens = tokens[:wildcard_at]
        if len(tokens) not in {2, 3, 4}:
            raise ValueError("use 172.27.*, 172.27.1, 172.27.1.* ou um CIDR")
        try:
            octets = [int(token) for token in tokens]
        except ValueError as exc:
            raise ValueError("faixa IPv4 inválida") from exc
        if any(value < 0 or value > 255 for value in octets):
            raise ValueError("faixa IPv4 inválida")
        prefix = {2: 16, 3: 24, 4: 32}[len(octets)]
        address = ".".join(str(item) for item in [*octets, *([0] * (4 - len(octets)))])
        network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)

    if not network.is_private:
        raise ValueError("a descoberta só aceita redes privadas")
    if not any(network.subnet_of(parent) for parent in authorized):
        allowed = ", ".join(str(item) for item in authorized)
        raise ValueError(f"a faixa {network} está fora do limite autorizado ({allowed})")
    return [network]


def _usable_count(network: ipaddress.IPv4Network) -> int:
    if network.prefixlen <= 30:
        return max(0, int(network.num_addresses) - 2)
    return int(network.num_addresses)


def _create_manual_run(settings: Settings, scope: str | None) -> FleetDiscoveryRunORM:
    networks = parse_discovery_scope(scope, settings=settings)
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
        previous = session.scalar(
            select(FleetDiscoveryRunORM).order_by(FleetDiscoveryRunORM.started_at.desc()).limit(1)
        )
        row = FleetDiscoveryRunORM(
            trigger="manual_rediscovery" if previous else "manual_initial",
            status="running",
            cidrs=[str(item) for item in networks],
            total_candidates=sum(_usable_count(item) for item in networks),
            metadata_payload={
                "strategy": "operator_selected_private_scope",
                "requested_scope": str(scope or "").strip() or None,
                "read_only_fingerprint": True,
                "started_by": "operator",
            },
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row


def _networks_from_run(run: FleetDiscoveryRunORM) -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    for raw in list(run.cidrs or []):
        parsed = ipaddress.ip_network(str(raw), strict=False)
        if not isinstance(parsed, ipaddress.IPv4Network):
            raise ValueError("run de descoberta contém rede não IPv4")
        networks.append(parsed)
    if not networks:
        raise ValueError("run de descoberta não possui CIDR persistido")
    return networks


def _scoped_tick_locked(settings: Settings) -> dict[str, Any]:
    cfg = _config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    run = _active_run()
    if run is None:
        return {"status": "idle", "detail": "nenhuma descoberta ativa"}
    networks = _networks_from_run(run)

    addresses, next_cidr, next_offset, finished_after_batch = _addresses_for_batch(
        networks,
        cursor_cidr=run.cursor_cidr,
        cursor_offset=run.cursor_offset,
        batch_size=int(cfg["batch_size"]),
    )
    if not addresses:
        with SessionLocal() as session:
            row = session.get(FleetDiscoveryRunORM, run.id)
            if row:
                row.status = "completed"
                row.completed_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(row)
                return {"status": "completed", "run": _run_dict(row)}
        return {"status": "completed"}

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=int(cfg["concurrency"]), thread_name_prefix="fleet-probe") as pool:
        futures = {pool.submit(probe_fleet_host, address, settings=settings): address for address in addresses}
        for future in as_completed(futures):
            address = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "address": address,
                        "ssh_port": int(settings.ssh_default_port),
                        "access_status": "error",
                        "accessible": False,
                        "error": redact_text(f"{type(exc).__name__}: {exc}")[:2000],
                    }
                )

    accessible = 0
    monitoring = 0
    for result in results:
        _upsert_probe_result(run.id, result)
        if result.get("accessible"):
            accessible += 1
            if (result.get("classification") or {}).get("monitoring_detected"):
                monitoring += 1

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        row = session.get(FleetDiscoveryRunORM, run.id)
        if not row:
            return {"status": "error", "detail": "run desapareceu durante o lote"}
        row.cursor_cidr = next_cidr
        row.cursor_offset = next_offset
        row.scanned = int(row.scanned or 0) + len(results)
        row.accessible = int(row.accessible or 0) + accessible
        row.inaccessible = int(row.inaccessible or 0) + (len(results) - accessible)
        row.monitoring_detected = int(row.monitoring_detected or 0) + monitoring
        row.updated_at = now
        if finished_after_batch:
            row.status = "completed"
            row.completed_at = now
        session.commit()
        session.refresh(row)
        run_state = _run_dict(row)

    return {
        "status": "completed" if finished_after_batch else "running",
        "batch": len(results),
        "batch_accessible": accessible,
        "batch_inaccessible": len(results) - accessible,
        "batch_monitoring": monitoring,
        "run": run_state,
    }


def scoped_fleet_discovery_tick(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    ensure_database_schema()
    if not _DISCOVERY_LOCK.acquire(blocking=False):
        return {"status": "busy", "detail": "descoberta já está em execução neste processo"}
    try:
        with engine.connect() as lock_connection:
            acquired = bool(
                lock_connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": _POSTGRES_ADVISORY_LOCK},
                )
            )
            if not acquired:
                return {"status": "busy", "detail": "outro worker está executando a descoberta"}
            try:
                return _scoped_tick_locked(settings)
            finally:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": _POSTGRES_ADVISORY_LOCK},
                )
    finally:
        _DISCOVERY_LOCK.release()


def _loop(settings: Settings) -> None:
    while True:
        try:
            result = scoped_fleet_discovery_tick(settings=settings)
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
    global _SCOPE_THREAD
    with _SCOPE_THREAD_LOCK:
        if _SCOPE_THREAD is not None and _SCOPE_THREAD.is_alive():
            return True
        thread = threading.Thread(target=_loop, args=(settings,), name="fleet-discovery-scoped", daemon=True)
        thread.start()
        _SCOPE_THREAD = thread
        return True


def start_fleet_discovery(*, scope: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    ensure_database_schema()
    existing = _active_run()
    run = existing or _create_manual_run(settings, scope)
    _launch(settings)
    return {
        "started": True,
        "resumed": existing is not None,
        "run_id": str(run.id),
        "trigger": run.trigger,
        "cidrs": list(run.cidrs or []),
        "status": fleet_control_status(settings=settings),
    }


def resume_active_fleet_discovery(*, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if _active_run() is None:
        return False
    return _launch(settings)
