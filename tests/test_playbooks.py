from app.services.adaptive_tools import describe_adaptive_tools
from app.services.command_catalog import validate_command
from app.services.noc_specialist_tools import describe_noc_specialist_tools
from app.services.operational_tools import describe_operational_tools
from app.services.playbooks import get_playbook, reload_playbooks, render_steps, select_playbook, use_playbook
from app.services.tool_registry import describe_tools


def test_expected_operational_playbooks_are_loaded():
    playbooks = reload_playbooks()
    ids = {item.id for item in playbooks}
    assert {
        "checkmk-systemd-socket-summary",
        "checkmk-automation-helper-stopped",
        "checkmk-agent-port-6556",
        "checkmk-rrdcached-stopped",
        "checkmk-container-unhealthy",
        "checkmk-snmp-timeout",
        "checkmk-service-vanished",
        "linux-filesystem-high",
        "linux-swap-high",
        "network-ssh-reset-peer",
        "network-vpn-tunnel-down",
        "project-linux-prod-std",
        "project-linux-monitoring",
        "project-management-interface",
        "bmc-hardware-alert",
        "snmp-daemon-stopped",
    } <= ids


def test_every_playbook_step_uses_registered_tool_or_safe_read_command():
    registered = {
        item["name"]
        for item in [
            *describe_tools(),
            *describe_adaptive_tools(),
            *describe_operational_tools(),
            *describe_noc_specialist_tools(),
        ]
    }
    for playbook in reload_playbooks():
        for step in playbook.steps:
            if step.get("tool"):
                assert step["tool"] in registered, f"{playbook.id}: {step['tool']}"
                continue
            command = str(step.get("command") or "")
            assert command, f"{playbook.id}: etapa sem tool ou command"
            allowed, reason, _ = validate_command(command)
            assert allowed, f"{playbook.id}: {command}: {reason}"
        for validation in playbook.validation_tools:
            if validation.get("tool"):
                assert validation["tool"] in registered, f"{playbook.id}: {validation['tool']}"


def test_socket_summary_selects_specific_playbook():
    playbook = select_playbook("Falha no sensor Systemd Socket Summary", "checkmk")
    assert playbook is not None
    assert playbook.id == "checkmk-systemd-socket-summary"
    assert "systemd.recover_unit" in playbook.allowed_corrections


def test_project_playbook_renders_infrastructure_from_env(monkeypatch):
    monkeypatch.setenv("SSH_SRV_VPN_IP", "10.99.0.1")
    monkeypatch.setenv("SSH_CMK05", "10.99.0.44")
    monkeypatch.setenv("API_WHATSAPP", "wa.exemplo.local")

    with use_playbook("manual", "project-linux-monitoring"):
        playbook = get_playbook("project-linux-monitoring")
        select_playbook("Projeto de monitoramento", "checkmk")
        steps = render_steps(playbook, {})

    text = str(steps)
    assert "10.99.0.1" in text
    assert "10.99.0.44" in text
    assert "wa.exemplo.local" in text
