from __future__ import annotations

from types import SimpleNamespace

from app.services.correction_policy import validate_correction
from app.services.noc_checkmk_runtime import _parse_output, is_green
from app.services.noc_communications import build_incident_communications, escalation_team
from app.services.noc_specialist_tools import describe_noc_specialist_tools
from app.services.noc_supervisor import _autonomy_eligible


def _autonomy_settings() -> SimpleNamespace:
    return SimpleNamespace(
        noc_autonomy_level=4,
        noc_self_heal_enabled=True,
        noc_self_heal_min_confidence=85,
        noc_self_heal_tools="systemd.recover_unit,checkmk.recover_omd_service",
    )


def test_autonomy_uses_effective_environment_from_investigation() -> None:
    incident = {"environment": "unknown"}
    result = {
        "environment_classification": {"environment": "monitoring"},
        "approval_token": "token",
        "review": {"approved": True},
        "analysis": {
            "confidence": 96,
            "proposed_actions": [
                {"tool": "systemd.recover_unit", "status": "proposed"},
            ],
        },
    }

    allowed, reason = _autonomy_eligible(incident, result, _autonomy_settings())

    assert allowed is True
    assert "baixo risco" in reason


def test_autonomy_never_self_heals_production() -> None:
    result = {
        "environment_classification": {"environment": "production"},
        "approval_token": "token",
        "review": {"approved": True},
        "analysis": {
            "confidence": 99,
            "proposed_actions": [
                {"tool": "systemd.recover_unit", "status": "proposed"},
            ],
        },
    }

    allowed, reason = _autonomy_eligible({"environment": "production"}, result, _autonomy_settings())

    assert allowed is False
    assert "production" in reason


def test_autonomy_rejects_tool_outside_allowlist() -> None:
    result = {
        "environment_classification": {"environment": "monitoring"},
        "approval_token": "token",
        "review": {"approved": True},
        "analysis": {
            "confidence": 99,
            "proposed_actions": [
                {"tool": "dangerous.restart_everything", "status": "proposed"},
            ],
        },
    }

    allowed, reason = _autonomy_eligible({"environment": "monitoring"}, result, _autonomy_settings())

    assert allowed is False
    assert "allowlist" in reason


def test_checkmk_runtime_parser_confirms_green_service() -> None:
    output = "\n".join(
        [
            "NOC_CONTEXT|checkmk-abc-25|abc",
            "NOC_STATE|srv01;Check_MK Agent;0;1786140000;OK - Agent responded",
        ]
    )

    runtime = _parse_output(output)

    assert runtime["found"] is True
    assert runtime["site"] == "abc"
    assert runtime["status"] == "healthy"
    assert is_green(runtime) is True


def test_checkmk_runtime_parser_keeps_critical_not_green() -> None:
    runtime = _parse_output(
        "NOC_CONTEXT|checkmk-abc-25|abc\n"
        "NOC_STATE|srv01;Check_MK Agent;2;1786140000;CRIT - Connection refused"
    )

    assert runtime["status"] == "critical"
    assert is_green(runtime) is False


def test_specialist_catalog_contains_snmp_and_bmc_tools() -> None:
    names = {item["name"] for item in describe_noc_specialist_tools()}

    assert "snmp.auto.system" in names
    assert "snmp.transport" in names
    assert "bmc.detect.local" in names
    assert "bmc.ipmi.sensors" in names
    assert "bmc.ipmi.sel" in names


def test_communication_fallback_generates_all_operator_surfaces() -> None:
    settings = SimpleNamespace(noc_communication_ai_enabled=False)
    incident = {
        "host": "srv-monitor",
        "service": "Check_MK Agent",
        "status": "needs_attention",
        "probable_cause": "porta 6556 indisponível",
        "conclusion": "é necessária intervenção fora do envelope automático",
    }

    communication = build_incident_communications(incident, settings=settings)
    messages = communication["messages"]

    assert messages["ticket"]
    assert messages["whatsapp"]
    assert messages["internal"]
    assert "Descrição do Problema" in messages["escalation"]
    assert messages["risk_letter"]
    assert communication["team"] == "noc_monitoring"


def test_escalation_routes_storage_and_hardware_domains() -> None:
    assert escalation_team(
        {"service": "Filesystem /", "last_output": "inode_bitmap unreadable"},
        {"probable_cause": "disk I/O error"},
    ) == "infra_storage"
    assert escalation_team(
        {"service": "Hardware Sensors", "last_output": "iDRAC power supply fault"},
        {"probable_cause": "physical PSU failure"},
    ) == "infra_hardware"


def test_correction_policy_expands_monitoring_but_keeps_container_blocked() -> None:
    assert validate_correction("systemctl restart snmpd.service").allowed is True
    assert validate_correction("docker exec checkmk-abc su - abc -c 'omd restart dcd'").allowed is True
    assert validate_correction("docker restart checkmk-abc").allowed is False
