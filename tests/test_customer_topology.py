from __future__ import annotations

from uuid import uuid4

from app.services.customer_topology import (
    get_customer_topology,
    reachable_nodes,
    save_customer_scope,
    select_automatic_related_nodes,
)


def _customer_name(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def test_same_internal_ip_is_isolated_between_customers() -> None:
    first_name = _customer_name("Empresa José A")
    second_name = _customer_name("Empresa José B")

    first = save_customer_scope(
        first_name,
        primary={
            "address": f"172.27.10.{int(uuid4().hex[:2], 16) % 200 + 1}",
            "ssh_port": 22,
            "label": "Monitoramento A",
            "role": "monitoring",
            "environment": "monitoring",
        },
        related_targets=[
            {
                "address": "10.45.1.24",
                "ssh_port": 22,
                "label": "Produção A",
                "role": "production",
                "environment": "production",
            }
        ],
    )
    second = save_customer_scope(
        second_name,
        primary={
            "address": f"172.27.20.{int(uuid4().hex[:2], 16) % 200 + 1}",
            "ssh_port": 22,
            "label": "Monitoramento B",
            "role": "monitoring",
            "environment": "monitoring",
        },
        related_targets=[
            {
                "address": "10.45.1.24",
                "ssh_port": 22,
                "label": "Produção B",
                "role": "production",
                "environment": "production",
            }
        ],
    )

    assert first["customer"]["id"] != second["customer"]["id"]
    assert [item["address"] for item in first["nodes"]].count("10.45.1.24") == 1
    assert [item["address"] for item in second["nodes"]].count("10.45.1.24") == 1
    assert first["routes"][0]["customer_id"] == first["customer"]["id"]
    assert second["routes"][0]["customer_id"] == second["customer"]["id"]


def test_reachable_nodes_never_cross_customer_boundary() -> None:
    name = _customer_name("Empresa Rotas")
    topology = save_customer_scope(
        name,
        primary={
            "address": "172.27.232.101",
            "ssh_port": 22,
            "label": "Monitoramento",
            "role": "monitoring",
            "environment": "monitoring",
        },
        related_targets=[
            {
                "address": "10.10.10.20",
                "ssh_port": 22,
                "label": "Standby",
                "role": "standby",
                "environment": "standby",
            },
            {
                "address": "10.10.10.30",
                "ssh_port": 22,
                "label": "Produção",
                "role": "production",
                "environment": "production",
            },
        ],
    )

    reachable = reachable_nodes(topology, "172.27.232.101", max_hops=1)

    assert {item["address"] for item in reachable} == {"10.10.10.20", "10.10.10.30"}
    assert all(item["customer_id"] == topology["customer"]["id"] for item in reachable)
    assert all(item["hops"] == 1 for item in reachable)


def test_reachability_respects_two_hop_ceiling() -> None:
    topology = {
        "customer": {"id": "customer-1", "name": "José"},
        "nodes": [
            {"id": "entry", "address": "172.27.1.1"},
            {"id": "middle", "address": "10.0.0.10"},
            {"id": "final", "address": "10.0.0.20"},
            {"id": "third", "address": "10.0.0.30"},
        ],
        "routes": [
            {"id": "r1", "source_node_id": "entry", "destination_node_id": "middle"},
            {"id": "r2", "source_node_id": "middle", "destination_node_id": "final"},
            {"id": "r3", "source_node_id": "final", "destination_node_id": "third"},
        ],
    }

    one_hop = reachable_nodes(topology, "172.27.1.1", max_hops=1)
    two_hops = reachable_nodes(topology, "172.27.1.1", max_hops=2)
    requested_three = reachable_nodes(topology, "172.27.1.1", max_hops=3)

    assert [item["address"] for item in one_hop] == ["10.0.0.10"]
    assert [item["address"] for item in two_hops] == ["10.0.0.10", "10.0.0.20"]
    assert [item["address"] for item in requested_three] == ["10.0.0.10", "10.0.0.20"]


def test_inconclusive_standby_analysis_selects_mapped_monitoring_host() -> None:
    name = _customer_name("Empresa Seleção")
    topology = save_customer_scope(
        name,
        primary={
            "address": "172.27.232.150",
            "ssh_port": 22,
            "label": "Standby",
            "role": "standby",
            "environment": "standby",
        },
        related_targets=[
            {
                "address": "10.20.30.40",
                "ssh_port": 22,
                "label": "Monitoramento",
                "role": "monitoring",
                "environment": "monitoring",
            }
        ],
    )
    result = {
        "context": "Alerta do Checkmk no standby; validar o site OMD",
        "analysis": {
            "status": "inconclusive",
            "summary": "O sintoma não pôde ser resolvido no standby.",
            "probable_cause": "A origem pode estar no servidor de monitoramento.",
            "recommendations": ["Validar o site Checkmk no monitoramento."],
        },
    }

    selected = select_automatic_related_nodes(result, topology, "172.27.232.150")

    assert selected
    assert selected[0]["address"] == "10.20.30.40"
    assert selected[0]["role"] == "monitoring"
    assert "monitoring" in selected[0]["selection_reason"]


def test_topology_can_be_resolved_by_internal_reference() -> None:
    name = _customer_name("Empresa Resolve")
    save_customer_scope(
        name,
        primary={
            "address": "172.27.232.180",
            "ssh_port": 22,
            "label": "Monitoramento Resolve",
            "role": "monitoring",
            "environment": "monitoring",
        },
        related_targets=[
            {
                "address": "10.99.0.15",
                "ssh_port": 2222,
                "label": "Aplicação Resolve",
                "role": "application",
                "environment": "production",
            }
        ],
    )

    topology = get_customer_topology(reference="10.99.0.15")

    assert topology["customer"]["name"] == name
    assert any(item["ssh_port"] == 2222 for item in topology["nodes"])
