from app.services.project_validation import build_project_plan


def _base(**overrides):
    payload = {
        "project_name": "Cliente X",
        "scenario": "linux_prod_std",
        "role": "production",
        "target_name": "Produção",
        "target_vpn_ip": "172.27.232.153",
        "target_internal_ip": "10.10.10.10",
        "os_family": "oracle8",
        "install_agent": True,
        "monitor1_ip": "10.17.181.1",
        "cmk05_ip": "10.17.181.44",
        "whatsapp_host": "ws.2comconsulting.com.br",
        "management_interface_type": "idrac",
        "management_interface_ip": "10.10.10.20",
        "related_hosts": [],
    }
    payload.update(overrides)
    return payload


def _commands(plan):
    return "\n".join(item["command"] for group in plan["groups"] for item in group["items"] if item["command"])


def test_prod_std_plan_uses_monitor1_and_target_vpn_for_6556():
    plan = build_project_plan(_base())
    commands = _commands(plan)

    assert "systemd-detect-virt" in commands
    assert "dmidecode -t1" in commands
    assert "cat /etc/*-release" in commands
    assert "nc -v -w5 10.17.181.1 6556" in commands
    assert "nc -v -w5 172.27.232.153 6556" in commands
    assert plan["playbook_id"] == "project-linux-prod-std"
    assert plan["summary"]["change_steps"] >= 2


def test_monitoring_plan_builds_internal_bidirectional_checks_and_monitor5():
    plan = build_project_plan(
        _base(
            scenario="linux_monitoring",
            role="monitoring",
            target_name="Monitor do cliente",
            target_internal_ip="192.168.1.20",
            related_hosts=[
                {"name": "Produção", "role": "production", "internal_ip": "192.168.1.10", "vpn_ip": "172.27.232.210"},
                {"name": "Standby", "role": "standby", "internal_ip": "192.168.1.11"},
            ],
        )
    )
    commands = _commands(plan)

    assert "nc -v -w5 192.168.1.10 6556" in commands
    assert "nc -v -w5 192.168.1.20 6556" in commands
    assert "nc -l 6557" in commands
    assert "nc -v -w5 10.17.181.44 6557" in commands
    assert "ws.2comconsulting.com.br 443" in commands
    assert any(target["reference"] == "172.27.232.210" for target in plan["execution_targets"])


def test_management_interface_uses_monitoring_server_when_selected():
    plan = build_project_plan(
        _base(
            scenario="management_interface",
            has_monitoring_server=True,
            monitoring_name="Monitor",
            monitoring_vpn_ip="172.27.232.200",
            monitoring_internal_ip="10.10.10.5",
            management_interface_type="ilom",
            management_interface_ip="10.10.10.247",
        )
    )

    monitor_group = next(group for group in plan["groups"] if group["key"] == "client_monitor")
    snmp = next(item for item in monitor_group["items"] if item["id"] == "management-snmp")
    assert "snmpwalk -v3" in snmp["command"]
    assert "10.10.10.247" in snmp["command"]
    assert plan["execution_targets"][0]["reference"] == "172.27.232.200"


def test_windows_plan_keeps_socat_and_internal_6556_checks_manual():
    plan = build_project_plan(
        _base(
            scenario="windows",
            os_family="windows",
            has_monitoring_server=True,
            monitoring_vpn_ip="172.27.232.77",
            monitoring_internal_ip="10.0.0.5",
            target_internal_ip="10.0.0.20",
        )
    )
    commands = _commands(plan)

    assert "socat TCP4-LISTEN:3389" in commands
    assert "Test-NetConnection 10.0.0.5 -Port 6556" in commands
    assert "nc -v -w5 10.0.0.20 6556" in commands
    assert plan["playbook_id"] == "project-windows"


def test_dns_plan_preserves_adjustments_as_manual_changes():
    plan = build_project_plan(
        _base(
            scenario="dns_vpn",
            gateway_dns="192.168.10.1",
            vpn_dns_name="vpn.oracledba.com.br",
        )
    )
    commands = _commands(plan)
    changes = [item for group in plan["groups"] for item in group["items"] if item["kind"] == "change"]

    assert "nslookup vpn.oracledba.com.br 8.8.8.8" in commands
    assert "nslookup vpn.oracledba.com.br 192.168.10.1" in commands
    assert "/var/log/openvpn_client.log" in commands
    assert all(item["approval_required"] for item in changes)
    assert "Não alterar DNS" in plan["execution_targets"][0]["objective"]
