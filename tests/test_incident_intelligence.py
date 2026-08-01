from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.conclusion_validator import validate_conclusion
from app.services.correction_comparison import build_before_after_comparison
from app.services.incident_correlation import correlate_alerts
from app.services.incident_intelligence import (
    build_dependency_map,
    classify_access_failure,
    evidence_freshness,
)
from app.services.incident_orchestration import enrich_incident_intelligence


def _base_result() -> dict:
    return {
        "target": "172.27.232.109",
        "hostname": "2com-monitor",
        "context": "Docker Container Health CRITICAL e OMD sf5 status CRITICAL",
        "profile": "checkmk",
        "connection": {
            "mode": "vpn_menu",
            "vpn_ip": "172.27.232.109",
            "client_name": "SALMO 91 MONITOR",
            "ssh_port": 22,
            "access_journey": [
                {"step": "bastion", "status": "completed"},
                {"step": "inventory", "status": "completed"},
                {"step": "selection", "status": "completed"},
                {"step": "confirmation", "status": "completed"},
                {"step": "authentication", "status": "completed"},
                {"step": "target_shell", "status": "completed"},
            ],
        },
        "inventory": {
            "client_name": "SALMO 91 MONITOR",
            "system_hostname": "2com-monitor",
            "vpn_ip": "172.27.232.109",
        },
        "history": [
            {
                "id": "old-1",
                "objective": "Process sf5 automation helpers CRITICAL",
                "created_at": "2026-07-31T12:00:00+00:00",
            }
        ],
        "similar_history": [],
        "evidence": [
            {
                "tool": "docker.list_unhealthy",
                "command": "docker ps -a --filter health=unhealthy",
                "status": "executed",
                "exit_code": 0,
                "stdout": "checkmk-sf5-25|image|Up 2 days (unhealthy)",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "tool": "checkmk.find_omd_service",
                "command": "omd status automation-helper",
                "status": "failed",
                "exit_code": 1,
                "stdout": "CONTAINER=checkmk-sf5-25 SITE=sf5 automation-helper: stopped",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "analysis": {
            "status": "attention",
            "confidence": 91,
            "facts": ["O shell do destino foi validado."],
            "probable_cause": "A VPN está indisponível e o container está parado.",
            "conclusion": "A falha ocorre na VPN.",
            "recommendations": ["Validar o processo automation-helper."],
            "evidence_map": [
                {"conclusion": "container ativo e unhealthy", "command": "docker ps", "evidence": "Up 2 days (unhealthy)"}
            ],
            "target_context": {
                "client_name": "SALMO 91 MONITOR",
                "vpn_ip": "172.27.232.109",
                "hostname": "2com-monitor",
            },
            "access_journey": [
                {"step": "target_shell", "status": "completed", "label": "Shell do alvo"}
            ],
        },
    }


def test_classifies_authentication_failure_without_blaming_vpn() -> None:
    journey = [
        {"step": "bastion", "status": "completed"},
        {"step": "inventory", "status": "completed"},
        {"step": "confirmation", "status": "completed"},
        {"step": "authentication", "status": "failed"},
    ]

    failure = classify_access_failure(PermissionError("Permission denied"), journey)

    assert failure["code"] == "target_authentication_failed"
    assert failure["vpn_reached"] is True
    assert failure["target_reached"] is False
    assert "não reiniciar a VPN" in failure["next_step"]


def test_correlates_checkmk_alerts_and_prioritizes_internal_process() -> None:
    correlation = correlate_alerts(_base_result())

    assert correlation["grouped"] is True
    assert correlation["site"] == "sf5"
    assert correlation["primary_alert"]["kind"] == "automation_helper"
    assert {item["kind"] for item in correlation["related_alerts"]} == {"omd_status", "container_health"}
    assert correlation["detected_kinds"] == ["automation_helper", "container_health", "omd_status"]


def test_does_not_group_alerts_from_different_sites() -> None:
    result = _base_result()
    result["history"] = [{"id": "other", "objective": "Process frj automation helpers CRITICAL"}]

    correlation = correlate_alerts(result)

    assert all(item.get("site") != "frj" for item in correlation["related_alerts"])
    assert correlation["primary_alert"]["kind"] == "omd_status"


def test_conclusion_validator_detects_vpn_and_container_contradictions() -> None:
    validation = validate_conclusion(_base_result())

    assert validation["verdict"] == "contradicted"
    assert any("shell do destino" in item.casefold() for item in validation["contradictions"])
    assert any("container ativo" in item.casefold() for item in validation["contradictions"])


def test_enrichment_caps_confidence_when_conclusion_is_contradicted() -> None:
    result = _base_result()

    enrich_incident_intelligence(result)

    assert result["analysis"]["confidence"] == 45
    intelligence = result["analysis"]["incident_intelligence"]
    assert intelligence["alert_correlation"]["primary_alert"]["kind"] == "automation_helper"
    assert intelligence["conclusion_validation"]["verdict"] == "contradicted"


def test_dependency_map_builds_client_to_process_chain() -> None:
    dependency = build_dependency_map(_base_result())

    labels = [item["label"] for item in dependency["nodes"]]
    assert labels[:3] == ["SALMO 91 MONITOR", "172.27.232.109", "2com-monitor"]
    assert "checkmk-sf5-25" in labels
    assert "sf5" in labels
    assert "automation-helper" in labels


def test_evidence_freshness_marks_old_entries() -> None:
    result = _base_result()
    now = datetime.now(timezone.utc)
    result["evidence"][0]["collected_at"] = (now - timedelta(hours=2)).isoformat()

    freshness = evidence_freshness(result, now=now)

    assert freshness["timestamp_coverage"] == 100
    assert freshness["stale"] == 1


def test_before_after_comparison_requires_post_validation() -> None:
    comparison = build_before_after_comparison(
        [
            {
                "tool": "systemd.recover_unit",
                "status": "validated",
                "exit_code": 0,
                "preconditions": [{"command": "systemctl is-active agent", "exit_code": 3, "stdout": "inactive"}],
                "validations": [{"command": "systemctl is-active agent", "exit_code": 0, "stdout": "active"}],
            }
        ]
    )

    assert comparison["status"] == "validated"
    assert comparison["validated_actions"] == 1
    assert comparison["changed_actions"] == 1
