from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select

from app.db.base import SessionLocal
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkSiteORM
from app.db.models import CustomerNodeORM, CustomerORM, CustomerRouteORM


def _node_payload(node: CustomerNodeORM) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "customer_id": str(node.customer_id),
        "address": node.address,
        "ssh_port": node.ssh_port,
        "hostname": node.hostname,
        "label": node.label,
        "role": node.role,
        "environment": node.environment,
        "direct_vpn": node.direct_vpn,
        "enabled": node.enabled,
        "metadata": dict(node.metadata_payload or {}),
        "first_seen_at": node.first_seen_at.isoformat() if node.first_seen_at else None,
        "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
    }


def _route_payload(route: CustomerRouteORM) -> dict[str, Any]:
    return {
        "id": str(route.id),
        "customer_id": str(route.customer_id),
        "source_node_id": str(route.source_node_id),
        "destination_node_id": str(route.destination_node_id),
        "route_type": route.route_type,
        "username": route.username,
        "priority": route.priority,
        "enabled": route.enabled,
        "last_verified_at": route.last_verified_at.isoformat() if route.last_verified_at else None,
        "metadata": dict(route.metadata_payload or {}),
    }


def _checkmk_role(host: CheckmkHostORM) -> str:
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
    if "application" in kind:
        return "application"
    return "other"


def _checkmk_node_payload(host: CheckmkHostORM, customer_id: uuid.UUID) -> dict[str, Any]:
    """Fallback visual baseado na fonte persistida ``checkmk_master_hosts``.

    A topologia materializada continua sendo usada para rotas. Este payload
    garante que a aba Clientes nunca esconda um host que o CMK05 já salvou.
    """

    return {
        "id": f"checkmk:{host.site_id}:{host.host_name}",
        "customer_id": str(customer_id),
        "address": host.internal_address or "0.0.0.0",
        "ssh_port": int(host.ssh_port or 22),
        "hostname": host.host_name,
        "label": host.host_name,
        "role": _checkmk_role(host),
        "environment": host.environment or "unknown",
        "direct_vpn": False,
        "enabled": True,
        "metadata": {
            "source": "checkmk_master_hosts",
            "site_id": host.site_id,
            "host_kind": host.host_kind,
            "checkmk_state": host.state,
            "persisted": True,
            "topology_fallback": True,
        },
        "first_seen_at": host.first_seen_at.isoformat() if host.first_seen_at else None,
        "last_seen_at": host.last_seen_at.isoformat() if host.last_seen_at else None,
    }


def _site_ids_for_customer(
    customer: CustomerORM,
    customer_nodes: list[dict[str, Any]],
    sites_by_alias: dict[str, list[str]],
) -> list[str]:
    ids: set[str] = set()
    for node in customer_nodes:
        site_id = str((node.get("metadata") or {}).get("site_id") or "").strip()
        if site_id:
            ids.add(site_id)
    names = [customer.name, *(customer.aliases or [])]
    for name in names:
        ids.update(sites_by_alias.get(str(name or "").strip().casefold(), []))
    return sorted(ids)


def _merge_checkmk_hosts(
    customer: CustomerORM,
    nodes: list[dict[str, Any]],
    site_ids: list[str],
    checkmk_hosts_by_site: dict[str, list[CheckmkHostORM]],
) -> tuple[list[dict[str, Any]], int]:
    merged = list(nodes)
    known = {
        (
            str((node.get("metadata") or {}).get("site_id") or ""),
            str((node.get("metadata") or {}).get("checkmk_host_name") or node.get("hostname") or "").casefold(),
        )
        for node in nodes
    }
    total = 0
    for site_id in site_ids:
        for host in checkmk_hosts_by_site.get(site_id, []):
            total += 1
            key = (site_id, host.host_name.casefold())
            if key in known:
                continue
            merged.append(_checkmk_node_payload(host, customer.id))
            known.add(key)
    return merged, total


def list_customer_overviews(*, query: str | None = None, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit), 300))
    normalized = str(query or "").strip()
    conditions = []
    if normalized:
        pattern = f"%{normalized}%"
        conditions.append(
            or_(
                CustomerORM.name.ilike(pattern),
                CustomerORM.key.ilike(pattern),
            )
        )

    with SessionLocal() as session:
        stmt = select(CustomerORM)
        if conditions:
            stmt = stmt.where(*conditions)
        customers = session.scalars(
            stmt.order_by(CustomerORM.updated_at.desc(), CustomerORM.name.asc()).limit(limit)
        ).all()
        if not customers:
            return {"total": 0, "items": []}
        ids = [item.id for item in customers]
        nodes = session.scalars(
            select(CustomerNodeORM)
            .where(CustomerNodeORM.customer_id.in_(ids), CustomerNodeORM.enabled.is_(True))
            .order_by(CustomerNodeORM.customer_id, CustomerNodeORM.role, CustomerNodeORM.label)
        ).all()
        routes = session.scalars(
            select(CustomerRouteORM)
            .where(CustomerRouteORM.customer_id.in_(ids), CustomerRouteORM.enabled.is_(True))
            .order_by(CustomerRouteORM.customer_id, CustomerRouteORM.priority)
        ).all()
        sites = session.scalars(
            select(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True)).order_by(CheckmkSiteORM.alias)
        ).all()

        nodes_by_customer: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        routes_by_customer: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            nodes_by_customer[node.customer_id].append(_node_payload(node))
        for route in routes:
            routes_by_customer[route.customer_id].append(_route_payload(route))

        sites_by_alias: dict[str, list[str]] = defaultdict(list)
        for site in sites:
            sites_by_alias[str(site.alias or "").strip().casefold()].append(site.site_id)

        site_ids_by_customer: dict[uuid.UUID, list[str]] = {
            customer.id: _site_ids_for_customer(customer, nodes_by_customer.get(customer.id, []), sites_by_alias)
            for customer in customers
        }
        all_site_ids = sorted({site_id for values in site_ids_by_customer.values() for site_id in values})
        checkmk_hosts_by_site: dict[str, list[CheckmkHostORM]] = defaultdict(list)
        if all_site_ids:
            checkmk_hosts = session.scalars(
                select(CheckmkHostORM)
                .where(CheckmkHostORM.site_id.in_(all_site_ids))
                .order_by(CheckmkHostORM.site_id, CheckmkHostORM.host_name)
            ).all()
            for host in checkmk_hosts:
                checkmk_hosts_by_site[host.site_id].append(host)

        items = []
        for customer in customers:
            base_nodes = nodes_by_customer.get(customer.id, [])
            customer_routes = routes_by_customer.get(customer.id, [])
            site_ids = site_ids_by_customer.get(customer.id, [])
            customer_nodes, checkmk_hosts_count = _merge_checkmk_hosts(
                customer,
                base_nodes,
                site_ids,
                checkmk_hosts_by_site,
            )
            role_counts: dict[str, int] = defaultdict(int)
            direct_hosts = 0
            for node in customer_nodes:
                role_counts[str(node.get("role") or "other")] += 1
                direct_hosts += 1 if node.get("direct_vpn") else 0
            verified_routes = sum(1 for route in customer_routes if route.get("last_verified_at"))
            entry = next(
                (
                    node
                    for node in customer_nodes
                    if node.get("direct_vpn") and node.get("role") == "monitoring"
                ),
                next((node for node in customer_nodes if node.get("direct_vpn")), None),
            )
            items.append(
                {
                    "id": str(customer.id),
                    "key": customer.key,
                    "name": customer.name,
                    "aliases": list(customer.aliases or []),
                    "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
                    "nodes_count": len(customer_nodes),
                    "checkmk_hosts_count": checkmk_hosts_count,
                    "site_ids": site_ids,
                    "routes_count": len(customer_routes),
                    "verified_routes_count": verified_routes,
                    "direct_hosts_count": direct_hosts,
                    "role_counts": dict(role_counts),
                    "entry_node": entry,
                    "nodes": customer_nodes,
                    "routes": customer_routes,
                    "inventory_source": "CMK05/master + topologia persistida",
                }
            )
    return {"total": len(items), "items": items}


def get_customer_overview(customer_id: str) -> dict[str, Any] | None:
    try:
        identifier = uuid.UUID(str(customer_id))
    except ValueError:
        return None
    with SessionLocal() as session:
        customer = session.get(CustomerORM, identifier)
        if not customer:
            return None
        nodes = session.scalars(
            select(CustomerNodeORM)
            .where(CustomerNodeORM.customer_id == identifier, CustomerNodeORM.enabled.is_(True))
            .order_by(CustomerNodeORM.role, CustomerNodeORM.label, CustomerNodeORM.address)
        ).all()
        routes = session.scalars(
            select(CustomerRouteORM)
            .where(CustomerRouteORM.customer_id == identifier, CustomerRouteORM.enabled.is_(True))
            .order_by(CustomerRouteORM.priority)
        ).all()
        sites = session.scalars(select(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True))).all()

        node_payloads = [_node_payload(node) for node in nodes]
        sites_by_alias: dict[str, list[str]] = defaultdict(list)
        for site in sites:
            sites_by_alias[str(site.alias or "").strip().casefold()].append(site.site_id)
        site_ids = _site_ids_for_customer(customer, node_payloads, sites_by_alias)
        checkmk_hosts_by_site: dict[str, list[CheckmkHostORM]] = defaultdict(list)
        if site_ids:
            checkmk_hosts = session.scalars(
                select(CheckmkHostORM)
                .where(CheckmkHostORM.site_id.in_(site_ids))
                .order_by(CheckmkHostORM.site_id, CheckmkHostORM.host_name)
            ).all()
            for host in checkmk_hosts:
                checkmk_hosts_by_site[host.site_id].append(host)
        merged_nodes, checkmk_hosts_count = _merge_checkmk_hosts(customer, node_payloads, site_ids, checkmk_hosts_by_site)

    return {
        "id": str(customer.id),
        "key": customer.key,
        "name": customer.name,
        "aliases": list(customer.aliases or []),
        "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
        "nodes_count": len(merged_nodes),
        "checkmk_hosts_count": checkmk_hosts_count,
        "site_ids": site_ids,
        "nodes": merged_nodes,
        "routes": [_route_payload(route) for route in routes],
        "inventory_source": "CMK05/master + topologia persistida",
    }
