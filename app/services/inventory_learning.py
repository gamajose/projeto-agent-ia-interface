from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.models import HostORM, InvestigationORM


_BACKFILL_LOCK = Lock()
_BACKFILL_COMPLETED = False


def _clean_text(value: Any, limit: int, fallback: str = "") -> str:
    text = str(value or fallback).strip()
    return text[:limit]


def _extract_host_port(reference: str, default_port: int) -> tuple[str | None, int]:
    value = str(reference or "").strip()
    port = int(default_port)
    if not value:
        return None, port

    bracketed = re.fullmatch(r"\[([^\]]+)]:(\d{1,5})", value)
    if bracketed:
        value = bracketed.group(1)
        port = int(bracketed.group(2))
    elif value.count(":") == 1:
        candidate, raw_port = value.rsplit(":", 1)
        if raw_port.isdigit():
            value = candidate.strip()
            port = int(raw_port)

    if not 1 <= port <= 65535:
        port = int(default_port)
    try:
        return str(ipaddress.ip_address(value)), port
    except ValueError:
        return None, port


def _internal_ips(identity: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    raw = identity.get("internal_ips")
    if isinstance(raw, list):
        candidates.extend(str(item) for item in raw)
    brief = identity.get("ip_brief")
    if isinstance(brief, str):
        candidates.extend(re.findall(r"(?<![0-9A-Fa-f:.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?", brief))
        candidates.extend(re.findall(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:/\d{1,3})?", brief))

    result: list[str] = []
    for candidate in candidates:
        value = candidate.strip().split("/", 1)[0]
        try:
            normalized = str(ipaddress.ip_address(value))
        except ValueError:
            continue
        if normalized not in result:
            result.append(normalized)
    return result


def _connection_client_name(result: dict[str, Any]) -> str:
    connection = dict(result.get("connection") or {})
    if not connection:
        connection = dict((result.get("automation") or {}).get("connection") or {})
    return _clean_text(connection.get("client_name"), 255)


def _sync_investigation_display_name(vpn_ip: str, client_name: str) -> int:
    """Atualiza o histórico antigo do mesmo IP com o nome vindo do menu VPN."""
    if not vpn_ip or not client_name:
        return 0
    with SessionLocal() as session:
        result = session.execute(
            update(InvestigationORM)
            .where(
                or_(
                    InvestigationORM.target == vpn_ip,
                    InvestigationORM.target.like(f"{vpn_ip}:%"),
                )
            )
            .values(hostname=client_name)
        )
        session.commit()
        return int(result.rowcount or 0)


def _upsert_host(
    *,
    vpn_ip: str,
    ssh_port: int,
    hostname: str,
    os_name: str,
    environment: str,
    host_type: str,
    internal_ips: list[str],
) -> HostORM:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        host = session.scalar(
            select(HostORM).where(
                HostORM.vpn_ip == vpn_ip,
                HostORM.ssh_port == ssh_port,
            )
        )
        if host is None:
            host = HostORM(
                host_type=_clean_text(host_type, 20, "server") or "server",
                vpn_ip=_clean_text(vpn_ip, 64),
                ssh_port=int(ssh_port),
            )
            session.add(host)
        host.hostname = _clean_text(hostname, 255, vpn_ip) or vpn_ip
        if os_name and os_name.casefold() not in {"unknown", "desconhecido", "none"}:
            host.os_name = _clean_text(os_name, 255)
        elif not host.os_name:
            host.os_name = "desconhecido"
        host.environment = _clean_text(environment, 20, "unknown") or "unknown"
        host.internal_ips = list(dict.fromkeys(internal_ips))
        host.last_seen_at = now
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            host = session.scalar(
                select(HostORM).where(
                    HostORM.vpn_ip == vpn_ip,
                    HostORM.ssh_port == ssh_port,
                )
            )
            if host is None:
                raise
            host.hostname = _clean_text(hostname, 255, vpn_ip) or vpn_ip
            host.environment = _clean_text(environment, 20, "unknown") or "unknown"
            host.last_seen_at = now
            session.commit()
        session.refresh(host)
        session.expunge(host)
        return host


def learn_result_inventory(
    result: dict[str, Any],
    *,
    resolved_host: str | None = None,
    ssh_port: int | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Registra um alvo concluído e devolve metadados públicos do aprendizado."""
    settings = settings or get_settings()
    ensure_database_schema()
    identity = dict(result.get("identity") or {})
    connection = dict((result.get("automation") or {}).get("connection") or {})
    if not connection:
        connection = dict(result.get("connection") or {})
    classification = dict(result.get("environment_classification") or {})
    candidate = str(resolved_host or connection.get("resolved_host") or result.get("target") or "").strip()
    parsed_host, parsed_port = _extract_host_port(candidate, int(ssh_port or connection.get("port") or settings.ssh_default_port))
    host = parsed_host or candidate
    port = int(ssh_port or connection.get("ssh_port") or connection.get("port") or parsed_port or settings.ssh_default_port)
    if not host:
        return {"saved": False, "detail": "O endereço resolvido do alvo não estava disponível."}

    client_name = _connection_client_name(result)
    system_hostname = _clean_text(identity.get("hostname") or result.get("hostname"), 255)
    display_name = client_name or system_hostname or host
    try:
        row = _upsert_host(
            vpn_ip=host,
            ssh_port=port,
            hostname=display_name,
            os_name=str(identity.get("os_name") or "desconhecido"),
            environment=str(classification.get("environment") or result.get("environment") or "unknown"),
            host_type=str(result.get("profile") or "server"),
            internal_ips=_internal_ips(identity),
        )
        history_updated = _sync_investigation_display_name(host, client_name) if client_name else 0
        return {
            "saved": True,
            "id": str(row.id),
            "vpn_ip": row.vpn_ip,
            "ssh_port": row.ssh_port,
            "hostname": row.hostname,
            "client_name": client_name or None,
            "system_hostname": system_hostname or None,
            "environment": row.environment,
            "history_updated": history_updated,
        }
    except Exception as exc:
        return {
            "saved": False,
            "detail": f"{type(exc).__name__}: {exc}",
            "vpn_ip": host,
            "ssh_port": port,
        }


def backfill_inventory_from_history(
    *,
    settings: Settings | None = None,
    limit: int = 1000,
    force: bool = False,
) -> dict[str, int]:
    """Aprende alvos IP de investigações antigas que ainda não estavam em ``hosts``."""
    global _BACKFILL_COMPLETED
    settings = settings or get_settings()
    ensure_database_schema()
    with _BACKFILL_LOCK:
        if _BACKFILL_COMPLETED and not force:
            return {"scanned": 0, "created_or_updated": 0, "skipped": 0}

        with SessionLocal() as session:
            rows = session.scalars(
                select(InvestigationORM)
                .order_by(InvestigationORM.created_at.desc())
                .limit(max(1, min(int(limit), 5000)))
            ).all()

        changed = 0
        skipped = 0
        for row in rows:
            analysis = dict(row.analysis or {})
            inventory = dict(analysis.get("inventory") or {})
            default_port = int(inventory.get("ssh_port") or settings.ssh_default_port)
            host, port = _extract_host_port(str(inventory.get("vpn_ip") or row.target or ""), default_port)
            if not host:
                skipped += 1
                continue
            try:
                _upsert_host(
                    vpn_ip=host,
                    ssh_port=port,
                    hostname=str(row.hostname or host),
                    os_name=str(inventory.get("os_name") or "desconhecido"),
                    environment=str(row.environment or "unknown"),
                    host_type=str(row.profile or "server"),
                    internal_ips=list(inventory.get("internal_ips") or []),
                )
                changed += 1
            except Exception:
                skipped += 1
        _BACKFILL_COMPLETED = True
        return {"scanned": len(rows), "created_or_updated": changed, "skipped": skipped}


def target_suggestions(query: str = "", *, limit: int = 12, settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    backfill_inventory_from_history(settings=settings)
    normalized = str(query or "").strip()
    pattern = f"%{normalized}%"
    with SessionLocal() as session:
        stmt = select(HostORM)
        if normalized:
            stmt = stmt.where(
                or_(
                    HostORM.vpn_ip.ilike(pattern),
                    HostORM.hostname.ilike(pattern),
                    HostORM.os_name.ilike(pattern),
                )
            )
        rows = session.scalars(
            stmt.order_by(HostORM.last_seen_at.desc()).limit(max(1, min(int(limit), 50)))
        ).all()
        return [
            {
                "value": row.vpn_ip,
                "vpn_ip": row.vpn_ip,
                "ssh_port": row.ssh_port,
                "hostname": row.hostname,
                "environment": row.environment,
                "os_name": row.os_name,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "source": "inventory",
            }
            for row in rows
        ]
