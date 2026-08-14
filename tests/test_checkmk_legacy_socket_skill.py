from app.core.policies import ActionType, EnvironmentType, classify_command
from app.services.correction_policy import validate_correction
from app.services.noc_skills import reload_noc_skills, select_noc_skill
from app.services.ssh import CommandResult
from app.services.tool_registry import execute_tool, resolve_tool


class SocketExecutor:
    def __init__(self, *, fail_listener: bool = False):
        self.fail_listener = fail_listener
        self.commands: list[tuple[str, bool]] = []

    def run(self, command, environment, approved=False, timeout=60):
        self.commands.append((command, approved))
        return CommandResult(command, 0, "ok\n", "")

    def run_sudo(self, command, environment, approved=False, timeout=60):
        self.commands.append((command, approved))
        if self.fail_listener and "6556" in command and "grep -Ei 'xinetd'" in command:
            return CommandResult(command, 1, "", "xinetd não é o listener")
        if "systemctl is-failed check_mk.socket" in command:
            return CommandResult(command, 0, "failed\n", "")
        if "<<<check_mk>>>" in command:
            return CommandResult(command, 0, "<<<check_mk>>>\n", "")
        return CommandResult(command, 0, "active\n", "")


def test_systemd_socket_summary_selects_known_skill() -> None:
    reload_noc_skills()
    skill = select_noc_skill(
        {
            "service": "Systemd Socket Summary",
            "output": "Total: 13, Disabled: 1, Failed: 1, 1 socket failed (check_mk)",
            "host": "sma-dbstandby",
            "host_address": "10.1.1.223",
        }
    )

    assert skill["id"] == "checkmk-systemd-socket-summary"
    assert skill["playbook_id"] == "checkmk-systemd-socket-summary"
    assert any("xinetd" in item.casefold() and "6556" in item for item in skill["knowledge"])


def test_legacy_socket_cleanup_is_narrowly_allowed_service_adjustment() -> None:
    plan = resolve_tool("checkmk.resolve_legacy_socket_conflict")

    assert plan.correction is True
    assert plan.preconditions_must_pass is True
    assert "systemctl disable --now check_mk.socket" in plan.command
    assert classify_command(plan.command) == ActionType.SERVICE_ADJUSTMENT
    decision = validate_correction(plan.command)
    assert decision.allowed is True
    assert decision.action_type == "checkmk_legacy_socket_cleanup"


def test_legacy_socket_cleanup_refuses_to_run_without_healthy_xinetd_listener() -> None:
    executor = SocketExecutor(fail_listener=True)

    result = execute_tool(
        executor,
        EnvironmentType.STANDBY,
        "checkmk.resolve_legacy_socket_conflict",
        approved=True,
    )

    assert result["status"] == "blocked"
    assert "pré-condições funcionais" in result["reason"]
    assert not any("systemctl disable --now check_mk.socket" in command for command, _ in executor.commands)


def test_legacy_socket_cleanup_runs_after_all_preconditions_pass() -> None:
    executor = SocketExecutor()

    result = execute_tool(
        executor,
        EnvironmentType.STANDBY,
        "checkmk.resolve_legacy_socket_conflict",
        approved=True,
    )

    assert result["status"] == "validated"
    correction_calls = [
        (command, approved)
        for command, approved in executor.commands
        if "systemctl disable --now check_mk.socket" in command
    ]
    assert correction_calls == [
        (
            "systemctl disable --now check_mk.socket && systemctl reset-failed check_mk.socket && systemctl daemon-reload",
            True,
        )
    ]


def test_other_systemd_disable_commands_remain_forbidden() -> None:
    decision = validate_correction(
        "systemctl disable --now sshd.service && systemctl reset-failed sshd.service && systemctl daemon-reload"
    )
    assert decision.allowed is False
