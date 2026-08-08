from __future__ import annotations

import ipaddress
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, engine, ensure_database_schema
from app.db.fleet_models import FleetAssetORM, FleetDiscoveryRunORM
from app.services.persistence import upsert_host
from app.services.redaction import redact_text
from app.services.runner import ResolvedTarget, build_executor
from app.services.runtime_env import runtime_bool, runtime_int, runtime_value


_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_THREAD: threading.Thread | None = None
_POSTGRES_ADVISORY_LOCK = 27427161


def _config(settings: Settings) -> dict[str, Any]:
    return {
        "enabled": runtime_bool("FLEET_DISCOVERY_ENABLED", True, settings=settings),
        "auto_start": runtime_bool("FLEET_DISCOVERY_AUTO_START", True, settings=settings),
        "cidrs": str(runtime_value("FLEET_DISCOVERY_CIDRS", "172.27.0.0/16", settings=settings) or "").strip(),
        "batch_size": runtime_int(
            "FLEET_DISCOVERY_BATCH_SIZE", 128, minimum=1, maximum=2048, settings=settings
        ),
        "concurrency": runtime_int(
            "FLEET_DISCOVERY_CONCURRENCY", 8, minimum=1, maximum=32, settings=settings
        ),
        "connect_timeout": runtime_int(
            "FLEET_DISCOVERY_CONNECT_TIMEOUT_SECONDS", 8, minimum=3, maximum=60, settings=settings
        ),
        "command_timeout": runtime_int(
            "FLEET_DISCOVERY_COMMAND_TIMEOUT_SECONDS", 10, minimum=3, maximum=60, settings=settings
        ),
        "rescan_hours": runtime_int(
            "FLEET_DISCOVERY_RESCAN_HOURS", 24, minimum=1, maximum=720, settings=settings
        ),
        "max_hosts": runtime_int(
            "FLEET_DISCOVERY_MAX_HOSTS", 65536, minimum=1, maximum=1_000_000, settings=settings
        ),
        "loop_sleep_seconds": runtime_int(
            "FLEET_DISCOVERY_LOOP_SLEEP_SECONDS", 2, minimum=1, maximum=300, settings=settings
        ),
        "monitor_threshold": runtime_int(
            "FLEET_DISCOVERY_MONITOR_THRESHOLD", 50, minimum=30, maximum=100, settings=settings
        ),
    }


def discovery_networks(settings: Settings | None = None) -> list[ipaddress.IPv4Network]:
    settings = settings or get_settings()
    cfg = _config(settings)
    networks: list[ipaddress.IPv4Network] = []
    for raw in re.split(r"[,;\s]+", cfg["cidrs"]):
        value = raw.strip()
        if not value:
            continue
        network = ipaddress.ip_network(value, strict=False)
        if network.version != 4:
            raise ValueError("A descoberta de frota aceita somente redes IPv4.")
        if not network.is_private:
            raise ValueError(f"FLEET_DISCOVERY_CIDRS só aceita redes privadas; rejeitada: {network}")
        if network not in networks:
            networks.append(network)
    if not networks:
        raise ValueError("FLEET_DISCOVERY_CIDRS não contém nenhuma rede válida.")
    total = sum(_usable_count(item) for item in networks)
    if total > int(cfg["max_hosts"]):
        raise ValueError(
            f"A descoberta contém {total} hosts, acima do limite FLEET_DISCOVERY_MAX_HOSTS={cfg['max_hosts']}."
        )
    return networks


def _usable_count(network: ipaddress.IPv4Network) -> int:
    if network.prefixlen <= 30:
        return max(0, int(network.num_addresses) - 2)
    return int(network.num_addresses)


def _host_at(network: ipaddress.IPv4Network, offset: int) -> str | None:
    count = _usable_count(network)
    if offset < 0 or offset >= count:
        return None
    base = int(network.network_address) + (1 if network.prefixlen <= 30 else 0)
    return str(ipaddress.IPv4Address(base + offset))


def _parse_omd_sites(value: str) -> list[str]:
    sites: list[str] = []
    for raw in str(value or "").splitlines():
        line = raw.strip()
        if not line or line.casefold().startswith("site ") or set(line) <= {"-", " ", "="}:
            continue
        token = line.split()[0].strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", token) and token.casefold() not in {"site", "version"}:
            if token not in sites:
                sites.append(token)
    return sites[:100]


def _label_environment(label: str) -> tuple[str, int]:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(label or "").casefold()).strip()
    tokens = set(normalized.split())
    standby = {"standby", "secundario", "secundária", "secundaria", "secondary", "replica"}
    production = {"prod", "producao", "produção", "primario", "primário", "primary"}
    monitoring = {"monitor", "monitoramento", "monitoring", "checkmk", "cmk", "omd"}
    if tokens & standby:
        return EnvironmentType.STANDBY.value, 90
    if tokens & production:
        return EnvironmentType.PRODUCTION.value, 90
    if tokens & monitoring:
        return EnvironmentType.MONITORING.value, 95
    return EnvironmentType.UNKNOWN.value, 0


def classify_fingerprint(
    fingerprint: dict[str, Any],
    *,
    monitor_threshold: int = 50,
) -> dict[str, Any]:
    client_name = str(fingerprint.get("client_name") or "")
    hostname = str(fingerprint.get("hostname") or "")
    label = f"{client_name} {hostname}".strip()
    sites = _parse_omd_sites(str(fingerprint.get("omd_sites") or ""))
    omd_dirs = [item.strip() for item in str(fingerprint.get("omd_dirs") or "").splitlines() if item.strip()]
    cmk_version = str(fingerprint.get("cmk_version") or "").strip()
    containers = str(fingerprint.get("checkmk_containers") or "").strip()
    processes = str(fingerprint.get("checkmk_processes") or "").strip()
    omd_dir = str(fingerprint.get("omd_dir") or "").strip().casefold() == "yes"

    score = 0
    reasons: list[str] = []
    if omd_dir:
        score += 35
        reasons.append("diretório /omd/sites presente")
    if sites or omd_dirs:
        score += 35
        reasons.append("site(s) OMD encontrados")
    if cmk_version:
        score += 20
        reasons.append("binário cmk identificado")
    if containers:
        score += 25
        reasons.append("container Checkmk identificado")
    if processes:
        score += 15
        reasons.append("processos Checkmk/OMD identificados")
    label_env, label_confidence = _label_environment(label)
    if label_env == EnvironmentType.MONITORING.value:
        score += 10
        reasons.append("nome do alvo indica monitoramento")
    score = min(100, score)
    monitoring_detected = score >= int(monitor_threshold)

    environment = label_env
    environment_confidence = label_confidence
    # Um servidor pode hospedar Checkmk e aplicação/produção ao mesmo tempo.
    # A presença do Checkmk é uma capacidade; ela não rebaixa um PROD/STANDBY
    # para MONITORING e, portanto, não libera self-healing indevido.
    if environment == EnvironmentType.MONITORING.value and not monitoring_detected:
        environment = EnvironmentType.UNKNOWN.value
        environment_confidence = 0
    if environment == EnvironmentType.UNKNOWN.value and monitoring_detected:
        # Sem um rótulo forte, ele pode ser um host misto. É coletor Checkmk,
        # mas continua UNKNOWN para políticas de mudança.
        environment_confidence = 0

    os_text = str(fingerprint.get("os_name") or "").casefold()
    pfsense = "pfsense" in os_text or "pfsense" in label.casefold()
    roles: list[str] = []
    if monitoring_detected:
        roles.append("monitoring")
    if pfsense:
        roles.append("firewall")
    if environment in {EnvironmentType.PRODUCTION.value, EnvironmentType.STANDBY.value}:
        roles.append(environment)
    if not roles:
        roles.append("server")

    capabilities: list[str] = ["ssh"]
    if monitoring_detected:
        capabilities.append("checkmk")
    if sites or omd_dirs:
        capabilities.append("omd")
    if containers:
        capabilities.append("checkmk_container")
    if pfsense:
        capabilities.append("pfsense")

    return {
        "environment": environment,
        "environment_confidence": environment_confidence,
        "roles": list(dict.fromkeys(roles)),
        "capabilities": list(dict.fromkeys(capabilities)),
        "monitoring_detected": monitoring_detected,
        "monitoring_confidence": score,
        "monitoring_reasons": reasons,
        "checkmk_sites": list(dict.fromkeys([*sites, *omd_dirs]))[:100],
    }


def _read(executor, command: str, timeout: int) -> str:
    try:
        result = executor.run(command, EnvironmentType.UNKNOWN, approved=False, timeout=timeout)
        return str(result.stdout or "").strip()[:12000]
    except Exception:
        return ""


def _os_name(raw: str) -> str:
    for line in str(raw or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')[:255]
    return str(raw or "").splitlines()[0][:255] if str(raw or "").strip() else "desconhecido"


def _internal_ips(raw: str) -> list[str]:
    output: list[str] = []
    for item in str(raw or "").split():
        try:
            value = str(ipaddress.ip_address(item.strip()))
        except ValueError:
            continue
        if value not in output:
            output.append(value)
    return output[:50]


def _access_failure(exc: Exception) -> str:
    text_value = f"{type(exc).__name__}: {exc}".casefold()
    if isinstance(exc, TimeoutError) or "timeout" in text_value:
        return "timeout"
    if isinstance(exc, PermissionError) or "permission denied" in text_value or "authentication" in text_value:
        return "auth_failed"
    if any(marker in text_value for marker in ("não encontrado", "nao encontrado", "não existe", "sem correspond")):
        return "not_found"
    return "error"


def probe_fleet_host(address: str, *, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    cfg = _config(settings)
    probe_settings = settings.model_copy(
        update={"ssh_connect_timeout": min(int(settings.ssh_connect_timeout), int(cfg["connect_timeout"]))}
    )
    target = ResolvedTarget(
        reference=address,
        host=address,
        port=int(settings.ssh_default_port),
        environment=EnvironmentType.UNKNOWN,
        inventory=None,
    )
    executor = build_executor(target, settings=probe_settings)
    if hasattr(executor, "vpn_menu_timeout"):
        executor.vpn_menu_timeout = max(5, min(int(getattr(executor, "vpn_menu_timeout", 45)), int(cfg["connect_timeout"])))

    try:
        executor.connect()
    except Exception as exc:
        executor.close()
        return {
            "address": address,
            "ssh_port": int(settings.ssh_default_port),
            "access_status": _access_failure(exc),
            "accessible": False,
            "error": redact_text(f"{type(exc).__name__}: {exc}")[:2000],
        }

    try:
        timeout = int(cfg["command_timeout"])
        connection = dict(getattr(executor, "connection_metadata", {}) or {})
        fingerprint = {
            "client_name": connection.get("client_name"),
            "hostname": _read(executor, "hostname 2>/dev/null || uname -n", timeout),
            "os_name": _read(executor, "cat /etc/os-release 2>/dev/null | head -n 20 || uname -a", timeout),
            "ip_addresses": _read(executor, "hostname -I 2>/dev/null || true", timeout),
            "omd_dir": _read(executor, "test -d /omd/sites && echo yes || echo no", timeout),
            "omd_sites": _read(executor, "omd sites 2>/dev/null || true", timeout),
            "omd_dirs": _read(
                executor,
                "find /omd/sites -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | head -n 100 || true",
                timeout,
            ),
            "cmk_version": _read(executor, "cmk --version 2>/dev/null | head -n 2 || true", timeout),
            "checkmk_containers": _read(
                executor,
                "(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null; podman ps --format '{{.Names}} {{.Image}}' 2>/dev/null) | grep -iE 'check.?mk|checkmk' | head -n 30 || true",
                timeout,
            ),
            "checkmk_processes": _read(
                executor,
                "ps -eo comm,args 2>/dev/null | grep -Ei 'cmc|nagios|mkeventd|rrdcached|check.?mk' | grep -v grep | head -n 40 || true",
                timeout,
            ),
        }
        fingerprint["os_name"] = _os_name(str(fingerprint.get("os_name") or ""))
        classification = classify_fingerprint(
            fingerprint,
            monitor_threshold=int(cfg["monitor_threshold"]),
        )
        return {
            "address": address,
            "ssh_port": int(connection.get("ssh_port") or settings.ssh_default_port),
            "access_status": "ok",
            "accessible": True,
            "client_name": str(connection.get("client_name") or "").strip() or None,
            "hostname": str(fingerprint.get("hostname") or "").strip()[:255] or None,
            "os_name": str(fingerprint.get("os_name") or "desconhecido")[:255],
            "internal_ips": _internal_ips(str(fingerprint.get("ip_addresses") or "")),
            "classification": classification,
            "fingerprint": {key: str(value or "")[:4000] for key, value in fingerprint.items()},
        }
    finally:
        executor.close()


def _upsert_probe_result(run_id, result: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    address = str(result["address"])
    port = int(result.get("ssh_port") or 22)
    inventory_host_id = None
    classification = dict(result.get("classification") or {})
    if result.get("accessible"):
        roles = list(classification.get("roles") or [])
        host_type = "firewall" if "firewall" in roles else "monitoring" if classification.get("monitoring_detected") else "server"
        host = upsert_host(
            host_type=host_type,
            vpn_ip=address,
            ssh_port=port,
            hostname=str(result.get("client_name") or result.get("hostname") or address)[:255],
            os_name=str(result.get("os_name") or "desconhecido")[:255],
            environment=str(classification.get("environment") or EnvironmentType.UNKNOWN.value),
            internal_ips=list(result.get("internal_ips") or []),
        )
        inventory_host_id = host.id

    with SessionLocal() as session:
        row = session.scalar(
            select(FleetAssetORM).where(FleetAssetORM.address == address, FleetAssetORM.ssh_port == port)
        )
        if row is None:
            row = FleetAssetORM(address=address, ssh_port=port)
            session.add(row)
        row.discovery_run_id = run_id
        row.inventory_host_id = inventory_host_id or row.inventory_host_id
        row.client_name = str(result.get("client_name") or "")[:255] or row.client_name
        row.hostname = str(result.get("hostname") or "")[:255] or row.hostname
        row.os_name = str(result.get("os_name") or "")[:255] or row.os_name
        row.access_status = str(result.get("access_status") or "unknown")[:40]
        row.environment = str(classification.get("environment") or row.environment or "unknown")[:30]
        row.roles = list(classification.get("roles") or row.roles or [])
        row.capabilities = list(classification.get("capabilities") or row.capabilities or [])
        row.monitoring_detected = bool(classification.get("monitoring_detected"))
        row.monitoring_confidence = int(classification.get("monitoring_confidence") or 0)
        row.checkmk_sites = list(classification.get("checkmk_sites") or [])
        row.evidence = {
            "monitoring_reasons": list(classification.get("monitoring_reasons") or []),
            "environment_confidence": int(classification.get("environment_confidence") or 0),
            "fingerprint": dict(result.get("fingerprint") or {}),
            "last_error": result.get("error"),
        }
        row.last_checked_at = now
        if result.get("accessible"):
            row.last_accessible_at = now
            row.consecutive_failures = 0
        else:
            row.consecutive_failures = int(row.consecutive_failures or 0) + 1
        session.commit()


def _run_dict(row: FleetDiscoveryRunORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "trigger": row.trigger,
        "status": row.status,
        "cidrs": list(row.cidrs or []),
        "cursor_cidr": row.cursor_cidr,
        "cursor_offset": row.cursor_offset,
        "total_candidates": row.total_candidates,
        "scanned": row.scanned,
        "accessible": row.accessible,
        "inaccessible": row.inaccessible,
        "monitoring_detected": row.monitoring_detected,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _get_or_create_run(settings: Settings, networks: list[ipaddress.IPv4Network]) -> FleetDiscoveryRunORM | None:
    cfg = _config(settings)
    now = datetime.now(timezone.utc)
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
        latest = session.scalar(
            select(FleetDiscoveryRunORM)
            .where(FleetDiscoveryRunORM.status == "completed")
            .order_by(FleetDiscoveryRunORM.completed_at.desc())
            .limit(1)
        )
        if latest and latest.completed_at and latest.completed_at > now - timedelta(hours=int(cfg["rescan_hours"])):
            return None
        trigger = "scheduled" if latest else "initial"
        run = FleetDiscoveryRunORM(
            trigger=trigger,
            status="running",
            cidrs=[str(item) for item in networks],
            total_candidates=sum(_usable_count(item) for item in networks),
            metadata_payload={"strategy": "full_private_cidr_sweep", "read_only_fingerprint": True},
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run


def _addresses_for_batch(
    networks: list[ipaddress.IPv4Network],
    *,
    cursor_cidr: int,
    cursor_offset: int,
    batch_size: int,
) -> tuple[list[str], int, int, bool]:
    output: list[str] = []
    cidr_index = int(cursor_cidr)
    offset = int(cursor_offset)
    while cidr_index < len(networks) and len(output) < batch_size:
        network = networks[cidr_index]
        count = _usable_count(network)
        while offset < count and len(output) < batch_size:
            address = _host_at(network, offset)
            offset += 1
            if address:
                output.append(address)
        if offset >= count:
            cidr_index += 1
            offset = 0
    return output, cidr_index, offset, cidr_index >= len(networks)


def _tick_locked(settings: Settings) -> dict[str, Any]:
    cfg = _config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    networks = discovery_networks(settings)
    run = _get_or_create_run(settings, networks)
    if run is None:
        return {"status": "idle", "detail": "rediscovery ainda não venceu"}

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
        return {"status": "completed", "run": _run_dict(run)}

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
        if row:
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
        else:
            run_state = None
    return {
        "status": "completed" if finished_after_batch else "running",
        "batch": len(results),
        "batch_accessible": accessible,
        "batch_inaccessible": len(results) - accessible,
        "batch_monitoring": monitoring,
        "run": run_state,
    }


def fleet_discovery_tick(*, settings: Settings | None = None) -> dict[str, Any]:
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
                return _tick_locked(settings)
            finally:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": _POSTGRES_ADVISORY_LOCK},
                )
    finally:
        _DISCOVERY_LOCK.release()


def fleet_discovery_status(*, limit_unreachable: int = 50) -> dict[str, Any]:
    ensure_database_schema()
    with SessionLocal() as session:
        latest = session.scalar(
            select(FleetDiscoveryRunORM).order_by(FleetDiscoveryRunORM.started_at.desc()).limit(1)
        )
        access_rows = session.execute(
            select(FleetAssetORM.access_status, func.count(FleetAssetORM.id))
            .group_by(FleetAssetORM.access_status)
        ).all()
        total_assets = int(session.scalar(select(func.count(FleetAssetORM.id))) or 0)
        monitoring_assets = int(
            session.scalar(
                select(func.count(FleetAssetORM.id)).where(FleetAssetORM.monitoring_detected.is_(True))
            )
            or 0
        )
        unreachable = session.scalars(
            select(FleetAssetORM)
            .where(FleetAssetORM.access_status != "ok")
            .order_by(FleetAssetORM.last_checked_at.desc())
            .limit(max(1, min(int(limit_unreachable), 200)))
        ).all()
        return {
            "run": _run_dict(latest),
            "assets": {
                "total": total_assets,
                "monitoring_detected": monitoring_assets,
                "by_access_status": {str(status): int(count) for status, count in access_rows},
            },
            "not_accessed": [
                {
                    "address": row.address,
                    "ssh_port": row.ssh_port,
                    "client_name": row.client_name,
                    "access_status": row.access_status,
                    "consecutive_failures": row.consecutive_failures,
                    "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
                    "error": (row.evidence or {}).get("last_error"),
                }
                for row in unreachable
            ],
        }


def _background_loop(settings: Settings) -> None:
    cfg = _config(settings)
    while True:
        try:
            result = fleet_discovery_tick(settings=settings)
            status = str(result.get("status") or "")
            if status == "running":
                delay = int(cfg["loop_sleep_seconds"])
            elif status in {"busy"}:
                delay = 10
            else:
                delay = min(300, max(30, int(cfg["loop_sleep_seconds"])))
        except Exception:
            delay = 30
        time.sleep(delay)


def start_fleet_discovery_background(*, settings: Settings | None = None) -> bool:
    global _DISCOVERY_THREAD
    settings = settings or get_settings()
    cfg = _config(settings)
    if not cfg["enabled"] or not cfg["auto_start"]:
        return False
    if _DISCOVERY_THREAD is not None and _DISCOVERY_THREAD.is_alive():
        return True
    thread = threading.Thread(
        target=_background_loop,
        args=(settings,),
        name="fleet-discovery",
        daemon=True,
    )
    thread.start()
    _DISCOVERY_THREAD = thread
    return True
