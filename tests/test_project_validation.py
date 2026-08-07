from app.services.project_validation import build_project_plan


def _facts(
    vpn_ip="172.27.232.153",
    *,
    internal_ip="10.10.10.10",
    os_family="oracle8",
    os_name="Oracle Linux Server 8.9",
    machine_type="física",
    virtualization="none",
    manufacturer="HPE",
    model="ProLiant ML110 Gen9",
    management_type="ilo",
    management_ip="10.10.10.20",
):
    return {
        "vpn_ip": vpn_ip,
        "reachable": True,
        "os_family": os_family,
        "os_name": os_name,
        "internal_ip": internal_ip,
        "machine_type": machine_type,
        "virtualization": virtualization,
        "manufacturer": manufacturer,
        "model": model,
        "management_type": management_type,
        "management_ip": management_ip,
        "agent_6556": "listening",
        "time_sync": "synchronized",
        "error": "",
    }


def _base(**overrides):
    payload = {
        "scenario": "linux_prod_std",
        "role": "production",
        "target_vpn_ip": "172.27.232.153",
        "install_agent": True,
        "has_monitoring_server": False,
        "related_hosts": [],
    }
    payload.update(overrides)
    return payload


def _discovery(target=None, monitor=None, related=None):
    return {
        "source": "test",
        "target": target or _facts(),
        "monitoring_server": monitor,
        "related_hosts": related or [],
    }


def _commands(plan):
    return "\n".join(item["command"] for group in plan["groups"] for item in group["items"] if item["command"])


def test_prod_std_only_needs_vpn_and_role_because_environment_is_discovered():
    plan = build_project_plan(_base(), discovery=_discovery())
    commands = _commands(plan)

    assert "systemd-detect-virt" in commands
    assert "dmidecode -t1" in commands
    assert "cat /etc/*-release" in commands
    assert "172.27.232.153 6556" in commands
    assert plan["target"]["internal_ip"] == "10.10.10.10"
    assert plan["discovery"]["target"]["os_name"] == "Oracle Linux Server 8.9"
    assert plan["discovery"]["target"]["management_type"] == "ilo"
    assert plan["playbook_id"] == "project-linux-prod-std"


def test_monitoring_discovers_internal_ips_from_related_vpn_hosts_and_builds_bidirectional_checks():
    monitor = _facts(
        vpn_ip="172.27.232.200",
        internal_ip="192.168.1.20",
        machine_type="virtual",
        virtualization="kvm",
        management_type="unknown",
        management_ip="",
    )
    prod = _facts(vpn_ip="172.27.232.210", internal_ip="192.168.1.10", management_ip="")
    prod["role"] = "production"
    standby = _facts(vpn_ip="172.27.232.211", internal_ip="192.168.1.11", management_ip="")
    standby["role"] = "standby"

    plan = build_project_plan(
        _base(
            scenario="linux_monitoring",
            role="monitoring",
            target_vpn_ip="172.27.232.200",
            related_hosts=[
                {"role": "production", "vpn_ip": "172.27.232.210"},
                {"role": "standby", "vpn_ip": "172.27.232.211"},
            ],
        ),
        discovery=_discovery(target=monitor, related=[prod, standby]),
    )
    commands = _commands(plan)

    assert "nc -v -w5 192.168.1.10 6556" in commands
    assert "nc -v -w5 192.168.1.11 6556" in commands
    assert "nc -v -w5 192.168.1.20 6556" in commands
    assert "nc -l 6557" in commands
    assert "ws.2comconsulting.com.br 443" in commands
    assert any(target["reference"] == "172.27.232.210" for target in plan["execution_targets"])
    assert any(target["reference"] == "172.27.232.211" for target in plan["execution_targets"])


def test_management_interface_is_discovered_and_shared_monitor_only_requires_vpn_ip():
    target = _facts(
        management_type="ilom",
        management_ip="10.10.10.247",
        manufacturer="Oracle Corporation",
        model="Oracle Server X8-2L",
    )
    monitor = _facts(vpn_ip="172.27.232.200", internal_ip="10.10.10.5", management_type="unknown", management_ip="")
    plan = build_project_plan(
        _base(
            scenario="management_interface",
            has_monitoring_server=True,
            monitoring_vpn_ip="172.27.232.200",
        ),
        discovery=_discovery(target=target, monitor=monitor),
    )

    monitor_group = next(group for group in plan["groups"] if group["key"] == "client_monitor")
    snmp = next(item for item in monitor_group["items"] if item["id"] == "management-snmp")
    assert "snmpwalk -v3" in snmp["command"]
    assert "10.10.10.247" in snmp["command"]
    assert "SNMP_V3_AUTH_PASSWORD" in snmp["command"]
    assert plan["discovery"]["target"]["management_type"] == "ilom"
    assert any(target["reference"] == "172.27.232.200" for target in plan["execution_targets"])


def test_windows_keeps_rdp_flow_without_requiring_os_or_internal_ip_fields():
    plan = build_project_plan(
        _base(
            scenario="windows",
            target_vpn_ip="172.27.232.88",
            has_monitoring_server=True,
            monitoring_vpn_ip="172.27.232.77",
        ),
        discovery=_discovery(
            target={
                "vpn_ip": "172.27.232.88",
                "reachable": None,
                "os_family": "windows",
                "os_name": "Windows — identificar por systeminfo",
                "internal_ip": "",
                "machine_type": "desconhecida",
                "virtualization": "unknown",
                "manufacturer": "",
                "model": "",
                "management_type": "unknown",
                "management_ip": "",
                "error": "",
            },
            monitor=_facts(vpn_ip="172.27.232.77", internal_ip="10.0.0.5"),
        ),
    )
    commands = _commands(plan)

    assert "socat TCP4-LISTEN:3389" in commands
    assert "IP_INTERNO_DESCOBERTO_DO_WINDOWS" in commands
    assert "systeminfo" in commands
    assert "ipconfig" in commands
    assert plan["playbook_id"] == "project-windows"


def test_dns_uses_discovered_os_to_offer_only_matching_adjustment():
    target = _facts(os_family="oracle8", os_name="Oracle Linux Server 8.9", management_ip="")
    plan = build_project_plan(
        _base(
            scenario="dns_vpn",
            gateway_dns="192.168.10.1",
            vpn_dns_name="vpn.oracledba.com.br",
        ),
        discovery=_discovery(target=target),
    )
    commands = _commands(plan)
    changes = [item for group in plan["groups"] for item in group["items"] if item["kind"] == "change"]

    assert "nslookup vpn.oracledba.com.br 8.8.8.8" in commands
    assert "nslookup vpn.oracledba.com.br 192.168.10.1" in commands
    assert "/var/log/openvpn_client.log" in commands
    assert "dns-change-ol8" in {item["id"] for item in changes}
    assert "dns-change-ol7" not in {item["id"] for item in changes}
    assert all(item["approval_required"] for item in changes)
    assert "Descubra a versão do SO automaticamente" in plan["execution_targets"][0]["objective"]
