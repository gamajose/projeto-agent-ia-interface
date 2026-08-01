from __future__ import annotations

import re
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import or_, select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.models import CustomerNodeORM, CustomerORM, CustomerRouteORM, HostORM


_VALID_ROLES = {
    "monitoring",
    "production",
    "standby",
    "database",
    "application",
    "firewall",
    "other",
}
_VALID_ENVIRONMENTS = {"monitoring", "production", "standby", "training", "unknown"}


def _clean(value: Any, limit: int = 255) -> str:
    return str(value or "").strip()[:limit]


def _key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:160] or "cliente-sem-nome"


def _role(value: Any) -> str:
    normalized = _clean(value, 40).casefold().replace("produção", "production")
    normalized = normalized.replace("monitoramento", "monitoring")
    normalized = normalized.replace("aplicação", "application")
    normalized = normalized.replace("banco", "database")
    return normalized if normalized in _VALID_ROLES else "other"


def _environment(value: Any) -> str:
    normalized = _clean(value, 30).casefold().replace("produção", "production")
    normalized = normalized.replace("monitoramento", "monitoring")
    return normalized if normalized in _VALID_ENVIRONMENTS else "unknown"


def _node_dict(node: CustomerNodeORM) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "customer_id": str(node.customer_id),
        "inventory_host_id": str(node.inventory_host_id) if node.inventory_host_id else None,
        "address": node.address,
        "ssh_port": node.ssh_port,
        "hostname": node.hostname,
        "label": node.label,
        "role": node.role,
        "environment": node.environment,
        "direct_vpn": node.direct_vpn,
        "enabled": node.enabled,
        "metadata": dict(node.metadata_payload or {}),
        "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
    }


def _route_dict(route: CustomerRouteORM) -> dict[str, Any]:
    return {
        "id": str(route.id),
        "customer_id": str(route.customer_id),
        "source_node_id": str(route.source_node_id),
        "destination_node_id": str(route.destination_node_id),
        "route_type": route.route_type,
        "username": route.username,
        "credential_ref": route.credential_ref,
        "priority": route.priority,
        "enabled": route.enabled,
        "metadata": dict(route.metadata_payload or {}),
        "last_verified_at": route.last_verified_at.isoformat() if route.last_verified_at else None,
    }


def _customer_dict(customer: CustomerORM) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "key": customer.key,
        "name": customer.name,
        "aliases": list(customer.aliases or []),
    }


def _find_customer(session, name: str) -> CustomerORM | None:
    normalized = _clean(name)
    if not normalized:
        return None
    customer = session.scalar(select(CustomerORM).where(CustomerORM.key == _key(normalized)))
    if customer:
        return customer
    return session.scalar(select(CustomerORM).where(CustomerORM.name.ilike(normalized)))


def _upsert_customer(session, name: str) -> CustomerORM:
    normalized = _clean(name) or "Cliente não identificado"
    customer = _find_customer(session, normalized)
    if customer is None:
        customer = CustomerORM(key=_key(normalized), name=normalized, aliases=[])
        session.add(customer)
        session.flush()
    elif len(normalized) > len(customer.name or ""):
        aliases = list(customer.aliases or [])
        if customer.name and customer.name not in aliases:
            aliases.append(customer.name)
        customer.name = normalized
        customer.aliases = aliases[-20:]
    return customer


def _inventory_host_id(session, address: str, port: int) -> uuid.UUID | None:
    host = session.scalar(
        select(HostORM).where(HostORM.vpn_ip == address, HostORM.ssh_port == int(port))
    )
    return host.id if host else None


def _upsert_node(
    session,
    customer: CustomerORM,
    payload: dict[str, Any],
    *,
    direct_vpn: bool | None = None,
) -> CustomerNodeORM:
    address = _clean(payload.get("address") or payload.get("reference"))
    if not address:
        raise ValueError("o endereço do host relacionado é obrigatório")
    port = int(payload.get("ssh_port") or 22)
    if not 1 <= port <= 65535:
        raise ValueError(f"porta SSH inválida para {address}: {port}")
    node = session.scalar(
        select(CustomerNodeORM).where(
            CustomerNodeORM.customer_id == customer.id,
            CustomerNodeORM.address == address,
            CustomerNodeORM.ssh_port == port,
        )
    )
    if node is None:
        node = CustomerNodeORM(
            customer_id=customer.id,
            address=address,
            ssh_port=port,
            role=_role(payload.get("role")),
            environment=_environment(payload.get("environment")),
        )
        session.add(node)
        session.flush()
    hostname = _clean(payload.get("hostname"))
    label = _clean(payload.get("label"))
    if hostname:
        node.hostname = hostname
    if label:
        node.label = label
    node.role = _role(payload.get("role") or node.role)
    node.environment = _environment(payload.get("environment") or node.environment)
    node.enabled = bool(payload.get("enabled", True))
    if direct_vpn is not None:
        node.direct_vpn = bool(direct_vpn)
    elif payload.get("direct_vpn") is not None:
        node.direct_vpn = bool(payload.get("direct_vpn"))
    if node.direct_vpn and node.inventory_host_id is None:
        node.inventory_host_id = _inventory_host_id(session, address, port)
    metadata = dict(node.metadata_payload or {})
    metadata.update(dict(payload.get("metadata") or {}))
    node.metadata_payload = metadata
    node.last_seen_at = datetime.now(timezone.utc)
    return node


def _find_node(session, customer_id: uuid.UUID, reference: str, port: int | None = None) -> CustomerNodeORM | None:
    value = _clean(reference)
    if not value:
        return None
    conditions = [
        CustomerNodeORM.address == value,
        CustomerNodeORM.hostname.ilike(value),
        CustomerNodeORM.label.ilike(value),
    ]
    stmt = select(CustomerNodeORM).where(
        CustomerNodeORM.customer_id == customer_id,
        or_(*conditions),
    )
    if port:
        stmt = stmt.where(CustomerNodeORM.ssh_port == int(port))
    return session.scalar(stmt.order_by(CustomerNodeORM.last_seen_at.desc()))


def _upsert_route(
    session,
    customer: CustomerORM,
    source: CustomerNodeORM,
    destination: CustomerNodeORM,
    payload: dict[str, Any],
) -> CustomerRouteORM:
    if source.id == destination.id:
        raise ValueError("a origem e o destino da rota SSH não podem ser o mesmo host")
    route_type = _clean(payload.get("route_type") or "ssh", 30).casefold()
    if route_type != "ssh":
        raise ValueError("somente rotas SSH internas são aceitas nesta versão")
    route = session.scalar(
        select(CustomerRouteORM).where(
            CustomerRouteORM.source_node_id == source.id,
            CustomerRouteORM.destination_node_id == destination.id,
            CustomerRouteORM.route_type == route_type,
        )
    )
    if route is None:
        route = CustomerRouteORM(
            customer_id=customer.id,
            source_node_id=source.id,
            destination_node_id=destination.id,
            route_type=route_type,
        )
        session.add(route)
    route.username = _clean(payload.get("username")) or route.username
    route.credential_ref = _clean(payload.get("credential_ref"), 120) or "SSH_DEFAULT_PASSWORD"
    route.priority = max(1, min(1000, int(payload.get("priority") or 100)))
    route.enabled = bool(payload.get("enabled", True))
    metadata = dict(route.metadata_payload or {})
    metadata.update(dict(payload.get("metadata") or {}))
    route.metadata_payload = metadata
    return route


def save_customer_scope(
    customer_name: str,
    *,
    primary: dict[str, Any],
    related_targets: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Persiste empresa, hosts e rotas sem armazenar senhas."""
    ensure_database_schema()
    with SessionLocal() as session:
        customer = _upsert_customer(session, customer_name)
        primary_node = _upsert_node(session, customer, primary, direct_vpn=True)
        nodes_by_reference: dict[str, CustomerNodeORM] = {
            primary_node.address.casefold(): primary_node,
        }
        if primary_node.hostname:
            nodes_by_reference[primary_node.hostname.casefold()] = primary_node

        pending_routes: list[tuple[CustomerNodeORM, dict[str, Any]]] = []
        for raw in related_targets:
            item = dict(raw or {})
            destination = _upsert_node(session, customer, item, direct_vpn=bool(item.get("direct_vpn", False)))
            nodes_by_reference[destination.address.casefold()] = destination
            if destination.hostname:
                nodes_by_reference[destination.hostname.casefold()] = destination
            pending_routes.append((destination, item))

        for destination, item in pending_routes:
            via = _clean(item.get("via"))
            source = nodes_by_reference.get(via.casefold()) if via else primary_node
            if source is None:
                source = _find_node(session, customer.id, via)
            if source is None:
                raise ValueError(f"o host de origem da rota não foi encontrado na empresa: {via}")
            _upsert_route(session, customer, source, destination, item)

        session.commit()
        customer_id = customer.id
    return get_customer_topology(customer_id=str(customer_id))


def get_customer_topology(
    *,
    customer_id: str | None = None,
    customer_name: str | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    ensure_database_schema()
    with SessionLocal() as session:
        customer: CustomerORM | None = None
        if customer_id:
            try:
                customer = session.get(CustomerORM, uuid.UUID(str(customer_id)))
            except ValueError:
                customer = None
        if customer is None and customer_name:
            customer = _find_customer(session, customer_name)
        if customer is None and reference:
            node = session.scalar(
                select(CustomerNodeORM)
                .where(
                    or_(
                        CustomerNodeORM.address == _clean(reference),
                        CustomerNodeORM.hostname.ilike(_clean(reference)),
                        CustomerNodeORM.label.ilike(_clean(reference)),
                    )
                )
                .order_by(CustomerNodeORM.last_seen_at.desc())
            )
            customer = session.get(CustomerORM, node.customer_id) if node else None
        if customer is None:
            return {"customer": None, "nodes": [], "routes": []}
        nodes = session.scalars(
            select(CustomerNodeORM)
            .where(CustomerNodeORM.customer_id == customer.id, CustomerNodeORM.enabled.is_(True))
            .order_by(CustomerNodeORM.direct_vpn.desc(), CustomerNodeORM.role, CustomerNodeORM.label)
        ).all()
        routes = session.scalars(
            select(CustomerRouteORM)
            .where(CustomerRouteORM.customer_id == customer.id, CustomerRouteORM.enabled.is_(True))
            .order_by(CustomerRouteORM.priority, CustomerRouteORM.created_at)
        ).all()
        return {
            "customer": _customer_dict(customer),
            "nodes": [_node_dict(node) for node in nodes],
            "routes": [_route_dict(route) for route in routes],
        }


def reachable_nodes(
    topology: dict[str, Any],
    source_reference: str,
    *,
    max_hops: int = 2,
) -> list[dict[str, Any]]:
    nodes = [dict(item) for item in topology.get("nodes") or []]
    routes = [dict(item) for item in topology.get("routes") or []]
    source_value = _clean(source_reference).casefold()
    source = next(
        (
            node
            for node in nodes
            if source_value
            in {
                _clean(node.get("id")).casefold(),
                _clean(node.get("address")).casefold(),
                _clean(node.get("hostname")).casefold(),
                _clean(node.get("label")).casefold(),
            }
        ),
        None,
    )
    if source is None:
        return []
    by_id = {_clean(node.get("id")): node for node in nodes}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        outgoing.setdefault(_clean(route.get("source_node_id")), []).append(route)

    visited = {_clean(source.get("id"))}
    queue: deque[tuple[str, int, list[str]]] = deque([(_clean(source.get("id")), 0, [])])
    result: list[dict[str, Any]] = []
    while queue:
        node_id, hops, path = queue.popleft()
        if hops >= max(1, min(int(max_hops), 2)):
            continue
        for route in outgoing.get(node_id, []):
            destination_id = _clean(route.get("destination_node_id"))
            destination = by_id.get(destination_id)
            if not destination or destination_id in visited:
                continue
            visited.add(destination_id)
            route_path = [*path, _clean(route.get("id"))]
            result.append({**destination, "hops": hops + 1, "route_path": route_path, "route": route})
            queue.append((destination_id, hops + 1, route_path))
    return result


def select_automatic_related_nodes(
    result: dict[str, Any],
    topology: dict[str, Any],
    source_reference: str,
    *,
    max_hosts: int = 3,
) -> list[dict[str, Any]]:
    """Escolhe somente hosts alcançáveis e da mesma empresa."""
    reachable = reachable_nodes(topology, source_reference, max_hops=2)
    if not reachable:
        return []
    analysis = dict(result.get("analysis") or {})
    text = " ".join(
        [
            _clean(result.get("context") or result.get("objective")),
            _clean(analysis.get("summary"), 4000),
            _clean(analysis.get("probable_cause"), 4000),
            _clean(analysis.get("conclusion"), 4000),
            " ".join(_clean(item, 1000) for item in analysis.get("recommendations") or []),
        ]
    ).casefold()
    source_node = next(
        (
            item
            for item in topology.get("nodes") or []
            if _clean(source_reference).casefold()
            in {
                _clean(item.get("address")).casefold(),
                _clean(item.get("hostname")).casefold(),
                _clean(item.get("label")).casefold(),
            }
        ),
        {},
    )
    requested_roles: list[str] = []
    if any(token in text for token in ("checkmk", "omd", "automation-helper", "sensor", "monitoramento", "container")):
        requested_roles.append("monitoring")
    if any(token in text for token in ("standby", "dataguard", "replica", "secundário", "secundario")):
        requested_roles.append("standby")
    if any(token in text for token in ("produção", "producao", "production", "banco", "database", "oracle", "aplicação", "aplicacao")):
        requested_roles.extend(["production", "database", "application"])
    if str(analysis.get("status") or "") == "inconclusive" and source_node.get("role") != "monitoring":
        requested_roles.append("monitoring")

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role in requested_roles:
        for node in reachable:
            if node.get("role") != role or node.get("id") in seen:
                continue
            selected.append({**node, "selection_reason": f"A análise indicou dependência do host com função {role}."})
            seen.add(str(node.get("id")))
            if len(selected) >= max(1, min(int(max_hosts), 3)):
                return selected
    return selected


def mark_route_verified(route_id: str) -> None:
    try:
        identifier = uuid.UUID(str(route_id))
    except ValueError:
        return
    with SessionLocal() as session:
        route = session.get(CustomerRouteORM, identifier)
        if route:
            route.last_verified_at = datetime.now(timezone.utc)
            session.commit()
