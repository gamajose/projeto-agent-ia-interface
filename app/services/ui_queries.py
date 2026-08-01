from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.db.base import SessionLocal
from app.db.models import HostORM, InvestigationORM, MonitoringMappingORM
from app.services.metrics import observe


def _investigation_item(row: Any) -> dict[str, Any]:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    analysis = dict(mapping.analysis or {})
    playbook = None
    for plan in mapping.plans or []:
        candidate = plan.get("playbook") if isinstance(plan, dict) else None
        if isinstance(candidate, dict) and candidate.get("id"):
            playbook = {
                "id": candidate.get("id"),
                "title": candidate.get("title") or candidate.get("id"),
            }
            break

    final_confidence = analysis.get("confidence")
    try:
        confidence = max(
            0,
            min(
                100,
                int(final_confidence if final_confidence is not None else mapping.confidence),
            ),
        )
    except (TypeError, ValueError):
        confidence = int(mapping.confidence or 0)
    multi_host = dict(analysis.get("multi_host") or {})
    return {
        "id": str(mapping.id),
        "target": mapping.target,
        "hostname": mapping.hostname,
        "objective": mapping.objective,
        "environment": mapping.environment,
        "mode": mapping.mode,
        "status": analysis.get("status") or mapping.status,
        "confidence": confidence,
        "profile": mapping.profile,
        "model": mapping.model,
        "duration_ms": mapping.duration_ms,
        "playbook": playbook,
        "summary": analysis.get("summary"),
        "probable_cause": analysis.get("probable_cause"),
        "multi_host": (
            {
                "enabled": True,
                "customer": multi_host.get("customer"),
                "hosts_count": len(multi_host.get("hosts") or []),
                "root_host": multi_host.get("root_host"),
            }
            if multi_host.get("enabled")
            else None
        ),
        "created_at": mapping.created_at.isoformat() if mapping.created_at else None,
    }


def list_investigations(
    *,
    limit: int = 50,
    offset: int = 0,
    query: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    conditions = [~InvestigationORM.analysis.has_key("multi_host_parent_id")]  # type: ignore[attr-defined]

    normalized_query = (query or "").strip()
    if normalized_query:
        pattern = f"%{normalized_query}%"
        conditions.append(
            or_(
                InvestigationORM.target.ilike(pattern),
                InvestigationORM.hostname.ilike(pattern),
                InvestigationORM.objective.ilike(pattern),
            )
        )
    if status:
        conditions.append(InvestigationORM.status == status)
    if mode:
        conditions.append(InvestigationORM.mode == mode)
    if environment:
        conditions.append(InvestigationORM.environment == environment)

    with SessionLocal() as session:
        count_stmt = select(func.count(InvestigationORM.id)).where(*conditions)
        rows_stmt = select(
            InvestigationORM.id,
            InvestigationORM.target,
            InvestigationORM.hostname,
            InvestigationORM.objective,
            InvestigationORM.environment,
            InvestigationORM.mode,
            InvestigationORM.status,
            InvestigationORM.confidence,
            InvestigationORM.profile,
            InvestigationORM.model,
            InvestigationORM.duration_ms,
            InvestigationORM.plans,
            InvestigationORM.analysis,
            InvestigationORM.created_at,
        ).where(*conditions)
        total = int(session.scalar(count_stmt) or 0)
        rows = session.execute(
            rows_stmt.order_by(InvestigationORM.created_at.desc()).offset(offset).limit(limit)
        ).all()
        observe("agent_ui_query_rows", len(rows), labels={"query": "investigations"})
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_investigation_item(row) for row in rows],
        }


def list_hosts(
    *,
    limit: int = 100,
    query: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 300))
    monitor_host = aliased(HostORM)
    conditions = []
    normalized_query = (query or "").strip()
    if normalized_query:
        pattern = f"%{normalized_query}%"
        conditions.append(
            or_(
                HostORM.vpn_ip.ilike(pattern),
                HostORM.hostname.ilike(pattern),
                HostORM.os_name.ilike(pattern),
                MonitoringMappingORM.site_name.ilike(pattern),
                MonitoringMappingORM.container_name.ilike(pattern),
                MonitoringMappingORM.checkmk_hostname.ilike(pattern),
            )
        )
    if environment:
        conditions.append(HostORM.environment == environment)

    stmt = (
        select(HostORM, MonitoringMappingORM, monitor_host)
        .outerjoin(
            MonitoringMappingORM,
            MonitoringMappingORM.affected_host_id == HostORM.id,
        )
        .outerjoin(
            monitor_host,
            monitor_host.id == MonitoringMappingORM.monitoring_host_id,
        )
    )
    if conditions:
        stmt = stmt.where(*conditions)

    with SessionLocal() as session:
        rows = session.execute(
            stmt.order_by(HostORM.last_seen_at.desc()).limit(limit)
        ).all()
        items = []
        for host, mapping, monitoring in rows:
            items.append(
                {
                    "id": str(host.id),
                    "vpn_ip": host.vpn_ip,
                    "ssh_port": host.ssh_port,
                    "hostname": host.hostname,
                    "host_type": host.host_type,
                    "os_name": host.os_name,
                    "environment": host.environment,
                    "internal_ips": list(host.internal_ips or []),
                    "last_seen_at": host.last_seen_at.isoformat() if host.last_seen_at else None,
                    "mapping": (
                        {
                            "site_name": mapping.site_name,
                            "container_name": mapping.container_name,
                            "checkmk_hostname": mapping.checkmk_hostname,
                            "checkmk_version": mapping.checkmk_version,
                            "same_server": mapping.same_server,
                            "monitoring_host": {
                                "hostname": monitoring.hostname if monitoring else None,
                                "vpn_ip": monitoring.vpn_ip if monitoring else None,
                            },
                        }
                        if mapping
                        else None
                    ),
                }
            )
        observe("agent_ui_query_rows", len(items), labels={"query": "hosts"})
        return {"total": len(items), "limit": limit, "items": items}
