from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, or_, select

from app.db.base import SessionLocal
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
            .where(CustomerNodeORM.customer_id.in_(ids))
            .order_by(CustomerNodeORM.customer_id, CustomerNodeORM.role, CustomerNodeORM.label)
        ).all()
        routes = session.scalars(
            select(CustomerRouteORM)
            .where(CustomerRouteORM.customer_id.in_(ids))
            .order_by(CustomerRouteORM.customer_id, CustomerRouteORM.priority)
        ).all()

    nodes_by_customer: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    routes_by_customer: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_customer[node.customer_id].append(_node_payload(node))
    for route in routes:
        routes_by_customer[route.customer_id].append(_route_payload(route))

    items = []
    for customer in customers:
        customer_nodes = nodes_by_customer.get(customer.id, [])
        customer_routes = routes_by_customer.get(customer.id, [])
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
                "routes_count": len(customer_routes),
                "verified_routes_count": verified_routes,
                "direct_hosts_count": direct_hosts,
                "role_counts": dict(role_counts),
                "entry_node": entry,
                "nodes": customer_nodes,
                "routes": customer_routes,
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
            .where(CustomerNodeORM.customer_id == identifier)
            .order_by(CustomerNodeORM.role, CustomerNodeORM.label, CustomerNodeORM.address)
        ).all()
        routes = session.scalars(
            select(CustomerRouteORM)
            .where(CustomerRouteORM.customer_id == identifier)
            .order_by(CustomerRouteORM.priority)
        ).all()
    return {
        "id": str(customer.id),
        "key": customer.key,
        "name": customer.name,
        "aliases": list(customer.aliases or []),
        "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
        "nodes": [_node_payload(node) for node in nodes],
        "routes": [_route_payload(route) for route in routes],
    }
