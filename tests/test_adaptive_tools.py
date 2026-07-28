from __future__ import annotations

from dataclasses import dataclass

from app.core.policies import EnvironmentType
from app.services.adaptive_orchestrator import (
    combined_tool_catalog,
    enrich_tool_result,
    parse_runtime_snapshot,
    recommend_tools,
    runtime_availability,
    tool_feedback,
)
from app.services.adaptive_tools import (
    execute_adaptive_tool,
    resolve_adaptive_tool,
)


SNAPSHOT = """
SNAPSHOT_VERSION=1
KERNEL=Linux 6.8.0 x86_64 GNU/Linux
OS_ID=ol
OS_NAME=Oracle Linux Server 8.8
INIT=systemd
BIN=systemctl
BIN=journalctl
BIN=ss
BIN=docker
BIN=ps
BIN=find
BIN=rpm
BIN=tracepath
SERVICE=sshd.service|loaded|active
SERVICE=check-mk-agent.socket|loaded|active
SERVICE=docker.service|loaded|active
LISTENER=tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTENER=tcp LISTEN 0 128 0.0.0.0:6556 0.0.0.0:*
CONTAINER_RUNTIME=docker
CONTAINER=docker|checkmk-lab|checkmk/check-mk-raw|Up 2 hours (healthy)
FILESYSTEM=/dev/mapper/root xfs 30G 10G 20G 34% /
""".strip()


def test_parse_runtime_snapshot_builds_context_from_actual_host_output() -> None:
    context = parse_runtime_snapshot(SNAPSHOT)

    assert context["os_id"] == "ol"
    assert context["init"] == "systemd"
    assert "docker" in context["binaries"]
    assert context["container_runtimes"] == ["docker"]
    assert context["containers"][0]["name"] == "checkmk-lab"
    assert context["summary"]["services"] == 3
    assert "check-mk-agent.socket" in context["capability_terms"]


def test_runtime_availability_is_derived_from_snapshot_instead_of_fixed_list() -> None:
    availability = runtime_availability(parse_runtime_snapshot(SNAPSHOT))

    assert availability["docker"] is True
    assert availability["tracepath"] is True
    assert "snmpwalk" not in availability


def test_combined_catalog_marks_tools_from_runtime_requirements() -> None:
    catalog = combined_tool_catalog(parse_runtime_snapshot(SNAPSHOT))
    by_name = {item["name"]: item for item in catalog}

    assert by_name["container.inventory"]["available"] is True
    assert by_name["network.path"]["available"] is True
    assert by_name["network.resolve"]["available"] is False
    assert by_name["runtime.snapshot"]["adaptive"] is True


def test_recommendations_follow_objective_runtime_and_history() -> None:
    context = parse_runtime_snapshot(SNAPSHOT)
    catalog = combined_tool_catalog(context)
    recommendations = recommend_tools(
        objective="Investigar socket do agente Checkmk na porta 6556 e buscar erros no serviço",
        runtime_context=context,
        catalog=catalog,
        history=[{"plans": [{"tools": [{"tool": "checkmk.inspect_agent_socket"}]}]}],
        evidence=[],
        executed={"runtime.snapshot"},
        limit=8,
    )
    names = [item["tool"] for item in recommendations]

    assert "checkmk.inspect_agent_socket" in names
    assert "service.search" in names or "logs.search" in names
    assert "runtime.snapshot" not in names


def test_failed_tool_is_penalized_and_receives_dynamic_alternatives() -> None:
    context = parse_runtime_snapshot(SNAPSHOT)
    catalog = combined_tool_catalog(context)
    failed = {
        "tool": "network.path",
        "category": "network",
        "status": "failed",
        "exit_code": 1,
        "stderr": "tracepath falhou",
    }
    enriched = enrich_tool_result(
        failed,
        catalog=catalog,
        executed_tools={"network.path"},
    )
    alternatives = [item["tool"] for item in enriched["alternative_tools"]]

    assert "network.path" not in alternatives
    assert alternatives

    recommendations = recommend_tools(
        objective="Validar comunicação e rota até o servidor remoto",
        runtime_context=context,
        catalog=catalog,
        evidence=[failed],
        executed=set(),
        limit=20,
    )
    scores = {item["tool"]: item["score"] for item in recommendations}
    if "network.path" in scores:
        assert scores["network.path"] < max(scores.values())


def test_tool_feedback_separates_success_failure_and_unavailable() -> None:
    feedback = tool_feedback(
        [
            {"tool": "service.search", "status": "executed", "exit_code": 0},
            {"tool": "network.path", "status": "failed", "exit_code": 1, "stderr": "falhou"},
            {"tool": "network.resolve", "status": "unavailable", "exit_code": 127},
            {"tool": "file.search", "status": "blocked", "exit_code": 255, "reason": "root inválido"},
        ]
    )

    assert feedback["successful"] == ["service.search"]
    assert feedback["failed"] == ["network.path"]
    assert feedback["unavailable"] == ["network.resolve"]
    assert feedback["blocked"] == ["file.search"]


def test_adaptive_tool_arguments_reject_shell_injection() -> None:
    command, _, _, _ = resolve_adaptive_tool("process.search", {"query": "automation-helper"})
    assert "automation-helper" in command

    blocked = execute_adaptive_tool(
        FakeExecutor(),
        EnvironmentType.TRAINING,
        "process.search",
        {"query": "checkmk; reboot"},
    )
    assert blocked["status"] == "blocked"
    assert blocked["exit_code"] == 255


def test_container_logs_is_read_only_and_never_controls_lifecycle() -> None:
    command, sudo, _, _ = resolve_adaptive_tool(
        "container.logs",
        {"container": "checkmk-lab", "runtime": "docker", "lines": 80},
    )

    assert command.startswith("docker logs")
    assert sudo is True
    assert "restart" not in command
    assert "stop" not in command
    assert "rm " not in command


@dataclass
class Result:
    exit_code: int = 0
    stdout: str = "ok"
    stderr: str = ""


class FakeExecutor:
    def run(self, command, environment, timeout=0):
        return Result()

    def run_sudo(self, command, environment, timeout=0, approved=False):
        return Result()
