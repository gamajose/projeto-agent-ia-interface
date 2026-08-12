from __future__ import annotations

from app.core.policies import ActionType, EnvironmentType, classify_command, evaluate_action
from app.services.noc_action_policy import _POLICY_DEFAULTS, classify_problem_category


def test_default_auto_correction_scope_is_monitoring_only() -> None:
    defaults = {item["category"]: bool(item["enabled"]) for item in _POLICY_DEFAULTS}
    assert defaults["checkmk_runtime"] is True
    assert defaults["monitoring_sensor"] is True
    assert defaults["host_check"] is True
    assert defaults["filesystem"] is False
    assert defaults["database"] is False
    assert defaults["memory"] is False
    assert defaults["network"] is False
    assert defaults["bmc_snmp"] is False
    assert defaults["server_reboot"] is False


def test_problem_category_routes_filesystem_database_and_monitoring() -> None:
    assert classify_problem_category({"service": "Filesystem /", "output": "96% used"}) == "filesystem"
    assert classify_problem_category({"service": "MSSQL job Entrada de Guias", "output": "Fail"}) == "database"
    assert classify_problem_category({"service": "ORA WINT.ARCHIVELOG RMAN Backup", "output": "late"}) == "database"
    assert classify_problem_category({"host": "checkmk-gar-25", "service": "OMD gar status"}) == "checkmk_runtime"
    assert classify_problem_category({"host": "db01", "service": "Check_MK Agent"}) == "host_check"
    assert classify_problem_category({"host": "db01", "service": "Check_MK Discovery"}) == "host_check"


def test_host_lifecycle_commands_are_absolute_denials() -> None:
    commands = (
        "reboot",
        "sudo reboot",
        "/sbin/reboot",
        "sudo /usr/sbin/reboot",
        "shutdown -r now",
        "sudo shutdown -h now",
        "poweroff",
        "halt",
        "init 6",
        "init 0",
        "telinit 6",
        "systemctl reboot",
        "systemctl poweroff",
        "sudo systemctl reboot",
        "sudo /usr/bin/systemctl --no-wall reboot",
        "systemctl --message=maintenance reboot",
        "systemctl isolate reboot.target",
        "sudo systemctl start poweroff.target",
        "exec shutdown -r now",
    )
    for command in commands:
        assert classify_command(command) == ActionType.HOST_REBOOT, command
        decision = evaluate_action(ActionType.HOST_REBOOT, EnvironmentType.MONITORING)
        assert decision.allowed is False
        assert decision.requires_approval is False
        assert decision.policy_code == "HOST_REBOOT_DENIED"
