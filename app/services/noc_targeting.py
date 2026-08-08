from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.core.policies import EnvironmentType
from app.db.base import SessionLocal
from app.db.models import HostORM, MonitoringMappingORM


_MONITOR_SCOPED_MARKERS = (
    "automation-helper",
    "automation helper",
    "rrdcached",
    "omd ",
    "omd status",
    "site status",
    "checkmk site",
    "check_mk site",
    "cmk site",
    "snmp timeout",
    "snmp response",
    "no snmp",
    "cannot fetch system description",
)


def service_scope(service: str, output: str = "") -> str:
    text = f"{service} {output}".casefold()
    return "monitoring" if any(marker in text for marker in _MONITOR_SCOPED_MARKERS) else "affected"


def _environment(value: Any) -> str:
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value)).value
    except ValueError:
        return EnvironmentType.UNKNOWN.value


def _host_dict(host: HostORM | None) -> dict[str, Any] | None:
    if not host:
        return None
    return {
        "id": str(host.id),
        "reference": host.vpn_ip,
        "vpn_ip": host.vpn_ip,
        "ssh_port": int(host.ssh_port or 22),
        "hostname": host.hostname,
        "environment": _environment(host.environment),
        "host_type": host.host_type,
    }


def mapped_targets(checkmk_host: str) -> dict[str, Any] | None:
    value = str(checkmk_host or "").strip()
    if not value:
        return None
    affected = aliased(HostORM)
    monitoring = aliased(HostORM)
    with SessionLocal() as session:
        row = session.execute(
            select(MonitoringMappingORM, affected, monitoring)
            .join(affected, affected.id == MonitoringMappingORM.affected_host_id)
            .join(monitoring, monitoring.id == MonitoringMappingORM.monitoring_host_id)
            .where(func.lower(MonitoringMappingORM.checkmk_hostname) == value.casefold())
            .order_by(MonitoringMappingORM.last_validated_at.desc())
            .limit(1)
        ).first()
        if row:
            mapping, affected_host, monitoring_host = row
            return {
                "source": "monitoring_mapping",
                "checkmk_host": value,
                "site": mapping.site_name,
                "same_server": bool(mapping.same_server),
                "affected": _host_dict(affected_host),
                "monitoring": _host_dict(monitoring_host),
            }

        host = session.scalar(
            select(HostORM)
            .where(func.lower(HostORM.hostname) == value.casefold())
            .order_by(HostORM.last_seen_at.desc())
            .limit(1)
        )
        if host:
            return {
                "source": "host_inventory",
                "checkmk_host": value,
                "site": None,
                "same_server": False,
                "affected": _host_dict(host),
                "monitoring": None,
            }
    return None


def resolve_noc_target(
    *,
    checkmk_host: str,
    service: str,
    output: str = "",
    explicit_target: str | None = None,
    requested_environment: EnvironmentType = EnvironmentType.UNKNOWN,
) -> dict[str, Any]:
    explicit = str(explicit_target or "").strip()
    if explicit:
        return {
            "reference": explicit,
            "ssh_port": None,
            "environment": requested_environment.value,
            "scope": "explicit",
            "source": "webhook_explicit_target",
            "affected": None,
            "monitoring": None,
        }

    mapping = mapped_targets(checkmk_host)
    scope = service_scope(service, output)
    if mapping:
        preferred = mapping.get("monitoring") if scope == "monitoring" else mapping.get("affected")
        fallback = mapping.get("affected") if scope == "monitoring" else mapping.get("monitoring")
        selected = preferred or fallback
        if selected:
            return {
                "reference": selected.get("reference"),
                "ssh_port": selected.get("ssh_port"),
                "environment": selected.get("environment") or requested_environment.value,
                "scope": scope,
                "source": mapping.get("source"),
                "same_server": mapping.get("same_server"),
                "site": mapping.get("site"),
                "affected": mapping.get("affected"),
                "monitoring": mapping.get("monitoring"),
            }

    return {
        "reference": str(checkmk_host or "").strip(),
        "ssh_port": None,
        "environment": requested_environment.value,
        "scope": scope,
        "source": "legacy_reference",
        "affected": None,
        "monitoring": None,
    }
