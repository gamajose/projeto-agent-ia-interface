from __future__ import annotations

from collections import defaultdict
from ipaddress import ip_address
from typing import Any

from sqlalchemy import select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkSiteORM
from app.services.customer_topology import _upsert_customer, _upsert_node, _upsert_route


def _routable_internal(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text or text in {"0.0.0.0", "127.0.0.1", "::1"}:
        return False
    try:
        parsed = ip_address(text)
    except ValueError:
        return False
    return not parsed.is_unspecified and not parsed.is_loopback


def _role(host: CheckmkHostORM) -> str:
    kind = str(host.host_kind or "server").casefold()
    environment = str(host.environment or "unknown").casefold()
    if kind == "firewall":
        return "firewall"
    if kind == "monitoring_local" or environment == "monitoring":
        return "monitoring"
    if environment == "standby":
        return "standby"
    if environment == "production":
        return "production"
    if "database" in kind:
        return "database"
    return "other"


def sync_checkmk_customers_from_inventory() -> dict[str, Any]:
    """Materializa o inventário do CMK05 na aba Clientes.

    A fonte continua sendo ``checkmk_master_sites`` + ``checkmk_master_hosts``.
    Nenhuma credencial é copiada. Endpoints compartilhados são persistidos para
    consulta, porém suas rotas internas ficam desabilitadas pelo site guard.
    """

    ensure_database_schema()
    with SessionLocal() as session:
        sites = session.scalars(
            select(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True)).order_by(CheckmkSiteORM.alias)
        ).all()
        hosts = session.scalars(
            select(CheckmkHostORM).order_by(CheckmkHostORM.site_id, CheckmkHostORM.host_name)
        ).all()
        hosts_by_site: dict[str, list[CheckmkHostORM]] = defaultdict(list)
        for host in hosts:
            hosts_by_site[host.site_id].append(host)

        customers = 0
        nodes = 0
        routes = 0
        for site in sites:
            customer = _upsert_customer(session, site.alias or site.site_id)
            primary = None
            if site.livestatus_host:
                primary = _upsert_node(
                    session,
                    customer,
                    {
                        "address": site.livestatus_host,
                        "ssh_port": 22,
                        "hostname": site.status_host or f"{site.site_id}-monitor",
                        "label": site.status_host or f"{site.alias} (MONITOR)",
                        "role": "monitoring",
                        "environment": "monitoring",
                        "enabled": True,
                        "metadata": {
                            "source": "checkmk_master",
                            "site_id": site.site_id,
                            "livestatus_port": site.livestatus_port,
                            "shared_endpoint": bool(site.shared_endpoint),
                            "site_guard": bool(site.shared_endpoint),
                        },
                    },
                    direct_vpn=not bool(site.shared_endpoint),
                )
                nodes += 1

            for host in hosts_by_site.get(site.site_id, []):
                address = str(host.internal_address or "").strip() or "0.0.0.0"
                destination = _upsert_node(
                    session,
                    customer,
                    {
                        "address": address,
                        "ssh_port": int(host.ssh_port or 22),
                        "hostname": host.host_name,
                        "label": host.host_name,
                        "role": _role(host),
                        "environment": host.environment or "unknown",
                        "enabled": True,
                        "metadata": {
                            "source": "checkmk_master",
                            "site_id": site.site_id,
                            "host_kind": host.host_kind,
                            "checkmk_state": host.state,
                            "routable": _routable_internal(address),
                            "shared_endpoint": bool(site.shared_endpoint),
                        },
                    },
                    direct_vpn=False,
                )
                nodes += 1
                if primary is None or destination.id == primary.id or not _routable_internal(address):
                    continue
                _upsert_route(
                    session,
                    customer,
                    primary,
                    destination,
                    {
                        "route_type": "ssh",
                        "priority": 100,
                        "enabled": not bool(site.shared_endpoint),
                        "credential_ref": "SSH_DEFAULT_PASSWORD",
                        "metadata": {
                            "source": "checkmk_master",
                            "site_id": site.site_id,
                            "site_guard": bool(site.shared_endpoint),
                            "auto_route": not bool(site.shared_endpoint),
                        },
                    },
                )
                routes += 1
            customers += 1
        session.commit()
    return {"customers": customers, "nodes": nodes, "routes": routes}
