from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from sqlalchemy import select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkSiteORM
from app.db.models import CustomerNodeORM
from app.services.customer_topology import _upsert_customer, _upsert_node, _upsert_route
from app.services.redaction import redact_text


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


def _is_checkmk_node_for_site(node: CustomerNodeORM, site_id: str) -> bool:
    metadata = dict(node.metadata_payload or {})
    return metadata.get("source") == "checkmk_master" and str(metadata.get("site_id") or "") == site_id


def _sync_site(site_id: str) -> dict[str, Any]:
    """Materializa um único site em sua própria transação.

    Um host inválido não pode apagar o restante do cliente nem interromper os
    outros 300+ sites. Cada host usa savepoint e falhas são devolvidas para
    auditoria do patrol.
    """

    result: dict[str, Any] = {
        "site_id": site_id,
        "customer": None,
        "hosts_source": 0,
        "nodes": 0,
        "routes": 0,
        "host_errors": [],
        "stale_disabled": 0,
    }
    with SessionLocal() as session:
        site = session.scalar(select(CheckmkSiteORM).where(CheckmkSiteORM.site_id == site_id))
        if site is None or not site.enabled:
            result["skipped"] = True
            return result

        hosts = session.scalars(
            select(CheckmkHostORM)
            .where(CheckmkHostORM.site_id == site.site_id)
            .order_by(CheckmkHostORM.host_name)
        ).all()
        result["hosts_source"] = len(hosts)

        customer = _upsert_customer(session, site.alias or site.site_id)
        result["customer"] = customer.name
        seen_node_ids: set[str] = set()

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
                        "source_kind": "site_entry",
                    },
                },
                direct_vpn=not bool(site.shared_endpoint),
            )
            session.flush()
            seen_node_ids.add(str(primary.id))
            result["nodes"] += 1

        for host in hosts:
            try:
                # Savepoint: um registro ruim não desfaz os hosts já persistidos
                # deste mesmo cliente.
                with session.begin_nested():
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
                                "checkmk_host_id": str(host.id),
                                "checkmk_host_name": host.host_name,
                                "internal_address": host.internal_address,
                                "host_kind": host.host_kind,
                                "checkmk_state": host.state,
                                "routable": _routable_internal(address),
                                "shared_endpoint": bool(site.shared_endpoint),
                                "source_kind": "checkmk_host",
                            },
                        },
                        direct_vpn=False,
                    )
                    session.flush()
                    seen_node_ids.add(str(destination.id))
                    result["nodes"] += 1
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
                                "checkmk_host_name": host.host_name,
                                "site_guard": bool(site.shared_endpoint),
                                "auto_route": not bool(site.shared_endpoint),
                            },
                        },
                    )
                    result["routes"] += 1
            except Exception as exc:
                result["host_errors"].append(
                    {
                        "host": host.host_name,
                        "address": host.internal_address,
                        "error": redact_text(f"{type(exc).__name__}: {exc}")[:600],
                    }
                )

        # Hosts antigos vindos do CMK05 não desaparecem do banco, mas ficam
        # desabilitados na topologia se deixaram de existir no snapshot atual.
        existing_nodes = session.scalars(
            select(CustomerNodeORM).where(CustomerNodeORM.customer_id == customer.id)
        ).all()
        for node in existing_nodes:
            if not _is_checkmk_node_for_site(node, site.site_id):
                continue
            if str(node.id) in seen_node_ids:
                continue
            if node.enabled:
                node.enabled = False
                result["stale_disabled"] += 1

        session.commit()
    return result


def sync_checkmk_customers_from_inventory() -> dict[str, Any]:
    """Persiste cliente + hosts do CMK05 na aba Clientes.

    A fonte de verdade continua sendo ``checkmk_master_sites`` e
    ``checkmk_master_hosts``. O sincronismo é isolado por ``site_id`` para que
    uma falha de um cliente nunca impeça os demais de serem salvos.

    Nenhuma senha, community ou secret é copiada. Endpoints compartilhados são
    persistidos para consulta, porém suas rotas internas ficam desabilitadas
    pelo site guard.
    """

    ensure_database_schema()
    with SessionLocal() as session:
        site_ids = list(
            session.scalars(
                select(CheckmkSiteORM.site_id)
                .where(CheckmkSiteORM.enabled.is_(True))
                .order_by(CheckmkSiteORM.alias)
            ).all()
        )

    totals: dict[str, Any] = {
        "sites_total": len(site_ids),
        "sites_synced": 0,
        "sites_failed": 0,
        "customers": 0,
        "hosts_source": 0,
        "nodes": 0,
        "routes": 0,
        "stale_disabled": 0,
        "host_errors": 0,
        "errors": [],
    }
    for site_id in site_ids:
        try:
            result = _sync_site(str(site_id))
            if result.get("skipped"):
                continue
            totals["sites_synced"] += 1
            totals["customers"] += 1
            totals["hosts_source"] += int(result.get("hosts_source") or 0)
            totals["nodes"] += int(result.get("nodes") or 0)
            totals["routes"] += int(result.get("routes") or 0)
            totals["stale_disabled"] += int(result.get("stale_disabled") or 0)
            host_errors = list(result.get("host_errors") or [])
            totals["host_errors"] += len(host_errors)
            if host_errors:
                totals["errors"].append(
                    {
                        "site_id": site_id,
                        "customer": result.get("customer"),
                        "host_errors": host_errors[:20],
                    }
                )
        except Exception as exc:
            totals["sites_failed"] += 1
            totals["errors"].append(
                {
                    "site_id": site_id,
                    "error": redact_text(f"{type(exc).__name__}: {exc}")[:1000],
                }
            )

    return totals
