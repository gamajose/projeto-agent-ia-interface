from __future__ import annotations

from pathlib import Path

from app.core.policies import EnvironmentType
from app.services.operational_tool_instrumentation import install_operational_tools
from app.services.operational_tools import (
    describe_operational_tools,
    execute_operational_tool,
    is_operational_tool,
)
from app.services.ssh import CommandResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Executor:
    host = "monitor-jose"

    def __init__(self) -> None:
        self.commands: list[tuple[str, bool, int]] = []

    def run(self, command, environment, approved=False, timeout=60):
        assert environment in {EnvironmentType.MONITORING, EnvironmentType.PRODUCTION}
        assert not approved
        self.commands.append((command, False, timeout))
        return CommandResult(command, 0, "ok\n", "")

    def run_sudo(self, command, environment, approved=False, timeout=60):
        assert not approved
        self.commands.append((command, True, timeout))
        return CommandResult(command, 0, "ok\n", "")


def test_catalog_contains_requested_operational_packages_and_no_corrections() -> None:
    rows = describe_operational_tools()
    names = {item["name"] for item in rows}
    required = {
        "checkmk.site.health",
        "checkmk.status.host",
        "checkmk.api.host",
        "pfsense.gateway.status",
        "pfsense.dpinger.logs",
        "pfsense.openvpn.status",
        "vpn.flapping.timeline",
        "network.mtr",
        "network.packet_capture",
        "network.mtu_test",
        "container.inspect",
        "container.health_history",
        "omd.status",
        "omd.logs",
        "redfish.system.health",
        "redfish.power.supplies",
        "redfish.event.log",
    }
    assert required <= names
    assert all(item["correction"] is False for item in rows)
    assert all(item["adaptive"] is True for item in rows)
    assert all(item["transport"] in {"ssh", "http"} for item in rows)


def test_packet_capture_requires_safe_filter_and_bounded_runtime() -> None:
    executor = _Executor()
    blocked = execute_operational_tool(
        executor,
        EnvironmentType.MONITORING,
        "network.packet_capture",
        {"interface": "any", "seconds": 10, "packets": 200, "filter": "tcp port 22 -w /tmp/a.pcap"},
    )
    assert blocked["status"] == "blocked"
    assert executor.commands == []

    executed = execute_operational_tool(
        executor,
        EnvironmentType.MONITORING,
        "network.packet_capture",
        {"interface": "any", "seconds": 30, "packets": 1000, "filter": "host 10.45.1.24 and port 161"},
    )
    assert executed["status"] == "executed"
    command, sudo, timeout = executor.commands[-1]
    assert sudo is True
    assert "timeout 30 tcpdump" in command
    assert "-s 128" in command
    assert "-c 1000" in command
    assert "-w" not in command
    assert timeout <= 40


def test_network_and_container_tools_validate_arguments_before_execution() -> None:
    executor = _Executor()
    invalid_host = execute_operational_tool(
        executor,
        EnvironmentType.PRODUCTION,
        "network.mtr",
        {"host": "10.0.0.1; reboot"},
    )
    assert invalid_host["status"] == "blocked"

    invalid_container = execute_operational_tool(
        executor,
        EnvironmentType.MONITORING,
        "container.inspect",
        {"container": "x; docker stop y"},
    )
    assert invalid_container["status"] == "blocked"
    assert not executor.commands


def test_flapping_tool_returns_structured_summary() -> None:
    class _FlappingExecutor(_Executor):
        def run_sudo(self, command, environment, approved=False, timeout=60):
            self.commands.append((command, True, timeout))
            output = "EVENT|gateway CACIQUE alarm latency 500ms loss 20%\nEVENT|gateway CACIQUE clear\nSUMMARY|up=1|down=1|loss_events=1\n"
            return CommandResult(command, 0, output, "")

    result = execute_operational_tool(
        _FlappingExecutor(),
        EnvironmentType.MONITORING,
        "vpn.flapping.timeline",
        {"query": "CACIQUE", "minutes": 60, "lines": 100},
    )
    assert result["status"] == "executed"
    assert result["normalized"]["event_count"] == 2
    assert result["normalized"]["summary"] == {"up": 1, "down": 1, "loss_events": 1}


def test_http_integrations_are_get_only_by_contract() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "operational_tools.py").read_text(encoding="utf-8")
    http_section = source.split("def _http_get", 1)[1].split("def _checkmk_http", 1)[0]
    assert "client.get(" in http_section
    for forbidden in ("client.post(", "client.put(", "client.patch(", "client.delete("):
        assert forbidden not in source
    assert "follow_redirects=False" in source


def test_instrumentation_routes_operational_tools_through_dynamic_agent() -> None:
    install_operational_tools()
    from app.services import dynamic_agent

    assert dynamic_agent.is_adaptive_tool("pfsense.gateway.status")
    executor = _Executor()
    result = dynamic_agent.execute_adaptive_tool(
        executor,
        EnvironmentType.MONITORING,
        "pfsense.routes",
        {},
    )
    assert result["operational"] is True
    assert result["correction"] is False


def test_catalog_is_available_to_planner() -> None:
    from app.services.adaptive_orchestrator import combined_tool_catalog

    catalog = combined_tool_catalog(
        {
            "binaries": ["docker", "tcpdump", "mtr", "ip", "nft"],
            "capability_terms": ["checkmk", "pfsense", "docker"],
        }
    )
    by_name = {item["name"]: item for item in catalog}
    assert by_name["network.packet_capture"]["available"] is True
    assert by_name["redfish.system.health"]["available"] is True
    assert by_name["redfish.system.health"]["transport"] == "http"
