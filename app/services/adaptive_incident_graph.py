from __future__ import annotations

import re
from typing import Any


def _unique(values: list[Any], *, limit: int = 80) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text[:1000])
        if len(result) >= limit:
            break
    return result


def _node(identifier: str, label: str, kind: str, **metadata: Any) -> dict[str, Any]:
    return {"id": identifier, "label": label, "kind": kind, "metadata": metadata}


def _edge(source: str, target: str, relation: str, **metadata: Any) -> dict[str, Any]:
    return {"source": source, "target": target, "relation": relation, "metadata": metadata}


def _alerts_from_objective(objective: str) -> list[dict[str, Any]]:
    rows: list[str] = []
    for raw in re.split(r"[\n;]+", objective or ""):
        text = " ".join(raw.split()).strip(" -•\t")
        if not text:
            continue
        lowered = text.casefold()
        if any(token in lowered for token in ("critical", "warning", "warn", "down", "stopped", "parado", "timeout", "unhealthy", "indispon")):
            rows.append(text[:500])
    if not rows and objective.strip():
        rows.append(" ".join(objective.split())[:500])

    alerts: list[dict[str, Any]] = []
    for index, text in enumerate(_unique(rows, limit=20)):
        lowered = text.casefold()
        domain = "other"
        component = "componente"
        if any(token in lowered for token in ("checkmk", "omd", "automation-helper", "xinetd", "6556")):
            domain = "checkmk"
        elif "snmp" in lowered or "161" in lowered:
            domain = "snmp"
        elif any(token in lowered for token in ("vpn", "openvpn", "ipsec", "gateway", "dpinger")):
            domain = "vpn"
        elif any(token in lowered for token in ("docker", "container", "unhealthy")):
            domain = "container"
        elif any(token in lowered for token in ("filesystem", "disco", "inode")):
            domain = "filesystem"
        elif any(token in lowered for token in ("memory", "memoria", "swap")):
            domain = "memory"

        known = (
            "automation-helper", "check-mk-agent.socket", "check-mk-agent", "xinetd",
            "snmpd", "bsnmpd", "openvpn", "ipsec", "dpinger", "docker",
        )
        for candidate in known:
            if candidate in lowered:
                component = candidate
                break
        alerts.append(
            {
                "id": f"alert-{index + 1}",
                "statement": text,
                "domain": domain,
                "component": component,
                "primary": index == 0,
            }
        )
    return alerts


def group_related_alerts(
    *,
    objective: str,
    adaptive_state: dict[str, Any] | None,
    existing_correlation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    alerts = _alerts_from_objective(objective)
    adaptive_state = dict(adaptive_state or {})
    leader = dict(adaptive_state.get("confirmed_cause") or adaptive_state.get("leader") or {})
    mechanism = str(leader.get("mechanism") or "").casefold()

    groups: list[dict[str, Any]] = []
    grouped_ids: set[str] = set()
    if alerts:
        by_domain: dict[str, list[dict[str, Any]]] = {}
        for alert in alerts:
            by_domain.setdefault(str(alert.get("domain") or "other"), []).append(alert)
        for domain, items in by_domain.items():
            if len(items) < 2 and domain == "other":
                continue
            relationship = "mesmo domínio operacional"
            if domain == "checkmk" and any(token in mechanism for token in ("processo interno", "site omd", "healthcheck")):
                relationship = "possíveis sintomas derivados do mesmo componente interno do site OMD"
            elif domain == "container" and "processo" in mechanism:
                relationship = "healthcheck possivelmente derivado de uma falha interna"
            elif domain == "vpn":
                relationship = "possível evento compartilhado de conectividade ou flapping"
            group_ids = [str(item["id"]) for item in items]
            grouped_ids.update(group_ids)
            groups.append(
                {
                    "id": f"group-{len(groups) + 1}",
                    "domain": domain,
                    "alert_ids": group_ids,
                    "relationship": relationship,
                    "root_hypothesis": leader.get("title") or None,
                }
            )

    existing = dict(existing_correlation or {})
    return {
        "version": 1,
        "alerts": alerts,
        "groups": groups,
        "grouped": bool(groups or existing.get("grouped")),
        "primary_alert_id": alerts[0]["id"] if alerts else None,
        "ungrouped_alert_ids": [item["id"] for item in alerts if item["id"] not in grouped_ids],
        "existing_correlation": existing,
    }


def build_adaptive_dependency_graph(
    *,
    fingerprint: dict[str, Any] | None,
    adaptive_state: dict[str, Any] | None,
    objective: str,
) -> dict[str, Any]:
    fingerprint = dict(fingerprint or {})
    adaptive_state = dict(adaptive_state or {})
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    known_nodes: set[str] = set()

    def add_node(item: dict[str, Any]) -> None:
        if item["id"] in known_nodes:
            return
        known_nodes.add(item["id"])
        nodes.append(item)

    hostname = str(fingerprint.get("hostname") or "Servidor investigado")
    add_node(_node("host", hostname, "host", environment=fingerprint.get("environment")))

    virtualization = str(fingerprint.get("virtualization") or "unknown")
    if virtualization in {"docker", "podman", "kubernetes"}:
        add_node(_node("runtime", virtualization, "container_runtime"))
        edges.append(_edge("host", "runtime", "hosts"))

    stack = list(fingerprint.get("monitoring_stack") or [])
    for name in stack:
        identifier = f"stack:{name}"
        add_node(_node(identifier, str(name), "monitoring_stack"))
        edges.append(_edge("runtime" if "runtime" in known_nodes else "host", identifier, "runs"))

    sites = list(fingerprint.get("omd_sites") or [])
    for site in sites:
        identifier = f"omd:{site}"
        add_node(_node(identifier, f"Site OMD {site}", "omd_site"))
        parent = "stack:checkmk" if "stack:checkmk" in known_nodes else "host"
        edges.append(_edge(parent, identifier, "contains"))

    leader = dict(adaptive_state.get("confirmed_cause") or adaptive_state.get("leader") or {})
    if leader:
        cause_id = f"hypothesis:{leader.get('id') or 'leader'}"
        add_node(
            _node(
                cause_id,
                str(leader.get("title") or "Hipótese líder"),
                "root_cause" if leader.get("status") == "confirmed" else "hypothesis",
                status=leader.get("status"),
                mechanism=leader.get("mechanism"),
            )
        )
        target = "omd:" + sites[0] if sites else ("stack:checkmk" if "stack:checkmk" in known_nodes else "host")
        edges.append(_edge(cause_id, target, "affects"))

    symptom = dict(adaptive_state.get("symptom") or {})
    statement = str(symptom.get("statement") or objective).strip()
    if statement:
        add_node(_node("symptom", statement[:240], "reported_symptom"))
        source = f"hypothesis:{leader.get('id') or 'leader'}" if leader else "host"
        if source in known_nodes:
            edges.append(_edge(source, "symptom", "produces"))

    checkmk_node = "stack:checkmk" if "stack:checkmk" in known_nodes else None
    if checkmk_node:
        internal_components = ("automation-helper", "agent-receiver", "xinetd", "apache", "nagios")
        runtime_services = " ".join(
            str(item) for item in (fingerprint.get("capabilities") or {}).get("services") or []
        ).casefold()
        for component in internal_components:
            if component not in runtime_services and component not in objective.casefold():
                continue
            identifier = f"component:{component}"
            add_node(_node(identifier, component, "service"))
            parent = "omd:" + sites[0] if sites else checkmk_node
            edges.append(_edge(parent, identifier, "depends_on"))

    return {
        "version": 1,
        "nodes": nodes,
        "edges": edges,
        "root_node": "host",
        "cause_node": f"hypothesis:{leader.get('id') or 'leader'}" if leader else None,
        "symptom_node": "symptom" if statement else None,
    }


def memory_guidance(similar_history: list[dict[str, Any]] | None) -> dict[str, Any]:
    cases = [item for item in (similar_history or []) if isinstance(item, dict)]
    verified = [item for item in cases if item.get("validation_state") == "verified"]
    rejected = [item for item in cases if item.get("validation_state") in {"rejected", "negative"}]
    return {
        "version": 1,
        "verified_cases": verified[:5],
        "negative_cases": rejected[:5],
        "guidance": [
            "Use casos anteriores apenas para priorizar testes; nunca como prova da causa atual.",
            "Quando a ocorrência for recorrente, diferencie recuperação temporária de causa definitiva.",
            "Uma correção anterior só é reutilizável após confirmar a mesma cadeia causal no ambiente atual.",
        ],
    }
