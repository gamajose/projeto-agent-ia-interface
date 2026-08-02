from __future__ import annotations

from app.services.adaptive_hypothesis_certainty import build_adaptive_hypothesis_state
from app.services.adaptive_incident_graph import (
    build_adaptive_dependency_graph,
    group_related_alerts,
)
from app.services.environment_fingerprint import build_environment_fingerprint


def _evidence(command: str, stdout: str, *, status: str = "executed", exit_code: int = 0) -> dict:
    return {
        "command": command,
        "tool": command.split()[0],
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": "",
        "normalized": {},
    }


def test_resource_exhaustion_becomes_confirmed_with_direct_evidence() -> None:
    state = build_adaptive_hypothesis_state(
        objective="Site de monitoramento indisponível e automation-helper parado",
        profile="checkmk",
        evidence=[
            _evidence("df -P /omd/sites/frj", "/dev/mapper/root 100% /omd/sites/frj"),
            _evidence("journalctl -u automation-helper", "No space left on device while creating temporary file"),
        ],
        runtime_context={"services": ["automation-helper"], "binaries": ["omd"]},
    )

    confirmed = state["confirmed_cause"]
    assert confirmed is not None
    assert confirmed["id"] == "resource_exhaustion"
    assert confirmed["status"] == "confirmed"
    assert state["stop_decision"]["ready"] is True
    assert state["causal_chain"][-1]["type"] == "reported_symptom"


def test_alert_state_is_not_created_as_root_cause_hypothesis() -> None:
    state = build_adaptive_hypothesis_state(
        objective="Process automation-helper stopped",
        profile="checkmk",
        evidence=[_evidence("omd status frj", "automation-helper stopped\nOverall state partially running")],
        runtime_context={"services": ["automation-helper"], "binaries": ["omd"]},
    )

    mechanisms = " ".join(str(item["mechanism"]) for item in state["hypotheses"]).casefold()
    assert "automation-helper está parado" not in mechanisms
    assert state["symptom"]["reported_state"] == "stopped"
    assert state["next_best_tests"]


def test_executed_tests_are_not_recommended_again() -> None:
    state = build_adaptive_hypothesis_state(
        objective="Investigar timeout SNMP",
        profile="linux_generic",
        evidence=[
            {
                "tool": "service.status",
                "command": "systemctl status snmpd",
                "status": "executed",
                "exit_code": 0,
                "stdout": "snmpd inactive",
                "stderr": "",
                "normalized": {},
            }
        ],
        runtime_context={"services": ["snmpd"], "listeners": []},
    )

    tools = [item["tool"] for item in state["next_best_tests"]]
    assert "service.status" not in tools


def test_environment_fingerprint_is_stable_for_same_capabilities() -> None:
    kwargs = {
        "identity": {"hostname": "monitor-01", "os_name": "Oracle Linux 8.10", "kernel": "5.15.0"},
        "runtime_context": {
            "os_name": "Oracle Linux 8.10",
            "binaries": ["systemctl", "omd", "docker"],
            "services": ["docker.service", "xinetd.service"],
            "listeners": ["tcp 6556"],
            "containers": ["checkmk-frj-25"],
            "discovery_status": "completed",
        },
        "evidence": [_evidence("omd sites", "frj 2.5.0p9 running")],
        "profile": "checkmk",
        "environment": {"environment": "monitoring", "confidence": 95},
    }
    first = build_environment_fingerprint(**kwargs)
    second = build_environment_fingerprint(**kwargs)

    assert first["signature"] == second["signature"]
    assert first["platform"]["family"] == "rhel"
    assert first["init_system"] == "systemd"
    assert "checkmk" in first["monitoring_stack"]
    assert first["environment"] == "monitoring"


def test_dependency_graph_connects_cause_to_symptom() -> None:
    adaptive = {
        "symptom": {"statement": "Site indisponível"},
        "confirmed_cause": {
            "id": "resource_exhaustion",
            "title": "Esgotamento de recurso",
            "mechanism": "Filesystem cheio impediu o processo de iniciar.",
            "status": "confirmed",
        },
    }
    fingerprint = {
        "hostname": "monitor-01",
        "environment": "monitoring",
        "virtualization": "docker",
        "monitoring_stack": ["checkmk"],
        "omd_sites": ["frj"],
        "capabilities": {"services": ["automation-helper"]},
    }
    graph = build_adaptive_dependency_graph(
        fingerprint=fingerprint,
        adaptive_state=adaptive,
        objective="Site indisponível",
    )

    node_ids = {item["id"] for item in graph["nodes"]}
    assert "host" in node_ids
    assert "hypothesis:resource_exhaustion" in node_ids
    assert "symptom" in node_ids
    assert any(edge["relation"] == "produces" for edge in graph["edges"])


def test_related_checkmk_alerts_are_grouped() -> None:
    grouping = group_related_alerts(
        objective=(
            "Docker Container Health CRITICAL; OMD frj Status CRITICAL; "
            "Process automation-helper stopped"
        ),
        adaptive_state={
            "leader": {
                "title": "Falha em componente interno do site Checkmk",
                "mechanism": "Um processo interno do site OMD produziu healthcheck degradado.",
            }
        },
    )

    assert grouping["grouped"] is True
    assert len(grouping["alerts"]) == 3
    assert any(group["domain"] == "checkmk" for group in grouping["groups"])
