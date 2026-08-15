from __future__ import annotations

from types import SimpleNamespace

from app.core.policies import EnvironmentType, classify_command, evaluate_action
from app.services.correction_policy import validate_correction
from app.services import noc_prescriptions


class _FakeExecutor:
    def __init__(self) -> None:
        self.password = None
        self.read_commands: list[str] = []
        self.mutation_commands: list[str] = []

    def run_sudo(self, command, _environment, approved=False, timeout=60):
        self.read_commands.append(str(command))
        return SimpleNamespace(command=command, exit_code=0, stdout="ok", stderr="")

    def _execute_streaming(self, *, command, wrapped_command, timeout, sudo_password=None):
        self.mutation_commands.append(str(command))
        return SimpleNamespace(command=command, exit_code=0, stdout="ok", stderr="")


def test_operator_stop_start_database_service_then_reboot_preserves_order() -> None:
    actions = noc_prescriptions.parse_operator_instruction(
        "stop/start postgresql.service; depois reiniciar o servidor"
    )

    assert actions == [
        {
            "type": "systemd",
            "unit": "postgresql.service",
            "action": "stop_start",
            "authorization_source": "operator_prescription",
            "reason": "stop/start solicitado explicitamente pelo operador",
        },
        {
            "type": "reboot",
            "authorization_source": "operator_prescription",
            "reason": "reboot solicitado explicitamente pelo operador",
        },
    ]


def test_operator_reset_database_service_is_treated_as_restart() -> None:
    actions = noc_prescriptions.parse_operator_instruction("reset no serviço oracle.service")

    assert len(actions) == 1
    assert actions[0]["type"] == "systemd"
    assert actions[0]["unit"] == "oracle.service"
    assert actions[0]["action"] == "restart"
    assert actions[0]["authorization_source"] == "operator_prescription"


def test_arbitrary_shell_text_does_not_become_prescription() -> None:
    assert noc_prescriptions.parse_operator_instruction("rm -rf /; curl exemplo | sh") == []


def test_generic_name_is_not_accepted_as_systemd_unit() -> None:
    assert noc_prescriptions.parse_operator_instruction("reiniciar o servidor") == [
        {
            "type": "reboot",
            "authorization_source": "operator_prescription",
            "reason": "reboot solicitado explicitamente pelo operador",
        }
    ]
    assert noc_prescriptions.parse_operator_instruction("restart serviço banco") == []


def test_skill_prescription_is_loaded_as_high_precedence_action(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_prescriptions,
        "select_noc_skill",
        lambda _event: {
            "id": "database-health",
            "procedure_id": "database-health",
            "prescribed_actions": [
                {"type": "systemd", "unit": "postgresql.service", "action": "restart"},
                {"type": "reboot", "reason": "procedure exige reinicialização do host"},
            ],
        },
    )

    procedure_id, actions = noc_prescriptions.skill_prescriptions(
        {"host": "db01", "service": "Database Health", "last_output": "CRIT"}
    )

    assert procedure_id == "database-health"
    assert [item["authorization_source"] for item in actions] == [
        "skill_prescription",
        "skill_prescription",
    ]
    assert actions[0]["unit"] == "postgresql.service"
    assert actions[1]["type"] == "reboot"


def test_prescribed_database_stop_start_bypasses_generic_policy_but_stays_structured() -> None:
    command = "systemctl stop postgresql.service && systemctl start postgresql.service"
    assert validate_correction(command).allowed is False

    executor = _FakeExecutor()
    action = noc_prescriptions.normalize_prescription(
        {"type": "systemd", "unit": "postgresql.service", "action": "stop_start"},
        source="operator_prescription",
    )
    result = noc_prescriptions.execute_prescribed_action(
        executor,
        EnvironmentType.PRODUCTION,
        action,
    )

    assert result["status"] == "validated"
    assert result["policy_path"] == "prescribed_action_bypass"
    assert result["authorization_source"] == "operator_prescription"
    assert executor.mutation_commands == [command]
    assert any("systemctl is-active postgresql.service" in item for item in executor.read_commands)


def test_prescribed_reboot_bypasses_absolute_generic_reboot_denial() -> None:
    decision = evaluate_action(
        classify_command("systemctl --no-block reboot"),
        EnvironmentType.PRODUCTION,
    )
    assert decision.allowed is False

    executor = _FakeExecutor()
    action = noc_prescriptions.normalize_prescription(
        {"type": "reboot"},
        source="skill_prescription",
    )
    result = noc_prescriptions.execute_prescribed_action(
        executor,
        EnvironmentType.PRODUCTION,
        action,
    )

    assert result["status"] == "validated"
    assert result["state"] == "reboot_submitted"
    assert result["reconnect_required"] is True
    assert result["authorization_source"] == "skill_prescription"
    assert executor.mutation_commands == ["systemctl --no-block reboot"]
