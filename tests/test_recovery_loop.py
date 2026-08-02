from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.policies import EnvironmentType
from app.services import recovery_loop
from app.services.recovery_loop import run_adaptive_recovery


class DummyExecutor:
    pass


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "agent_recovery_max_rounds": 3,
        "agent_recovery_max_actions": 6,
        "agent_recovery_max_diagnostics_per_round": 4,
        "agent_recovery_max_repeated_action": 2,
        "ai_reviewer_required_for_corrections": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _action(tool: str = "checkmk.recover_omd_service") -> dict[str, Any]:
    return {
        "description": "Recuperar automation-helper",
        "tool": tool,
        "arguments": {
            "container": "checkmk-frj-25",
            "site": "frj",
            "service": "automation-helper",
            "action": "start",
        },
        "evidence_reason": "A causa foi sustentada pelos logs do site.",
        "status": "proposed",
    }


def _scope(*tools: str) -> dict[str, Any]:
    return {
        "target": "172.27.1.10",
        "environment": "monitoring",
        "allowed_correction_tools": list(tools),
        "same_target_only": True,
        "database_access": False,
        "server_reboot": False,
        "container_lifecycle": False,
    }


def test_validated_action_finishes_recovery(monkeypatch) -> None:
    monkeypatch.setattr(
        recovery_loop,
        "execute_tool",
        lambda *args, **kwargs: {
            "tool": args[2],
            "status": "validated",
            "exit_code": 0,
            "stdout": "automation-helper running",
            "stderr": "",
            "validations": [{"exit_code": 0, "stdout": "running"}],
        },
    )

    result = run_adaptive_recovery(
        executor=DummyExecutor(),
        environment=EnvironmentType.MONITORING,
        initial_actions=[_action()],
        analysis={"probable_cause": "falha temporária da dependência"},
        evidence=[],
        scope=_scope("checkmk.recover_omd_service"),
        settings=_settings(),
    )

    assert result["status"] == "validated"
    assert result["state"] == "resolved_and_validated"
    assert len(result["results"]) == 1
    assert result["blockers"] == []
    assert result["new_approval_required"] is False


def test_failed_action_becomes_evidence_and_is_replanned(monkeypatch) -> None:
    correction_calls = 0

    def fake_execute(_executor, _environment, tool, arguments, *, approved=False):
        nonlocal correction_calls
        if tool == "systemd.inspect_unit":
            assert approved is False
            return {
                "tool": tool,
                "arguments": arguments,
                "status": "executed",
                "exit_code": 0,
                "stdout": "ActiveState=inactive\nResult=exit-code",
                "stderr": "",
            }
        correction_calls += 1
        assert approved is True
        if correction_calls == 1:
            return {
                "tool": tool,
                "status": "failed",
                "exit_code": 1,
                "stdout": "",
                "stderr": "Permission denied: /omd/sites/frj/tmp",
                "validations": [],
            }
        return {
            "tool": tool,
            "status": "validated",
            "exit_code": 0,
            "stdout": "automation-helper running",
            "stderr": "",
            "validations": [{"exit_code": 0, "stdout": "running"}],
        }

    def fake_model_call(_prompt, purpose, provider_name=None):
        del provider_name
        if purpose.startswith("recovery_diagnosis"):
            return {
                "blocker_summary": "Permissão negada no diretório temporário",
                "new_symptom": "Permission denied",
                "causal_link": "O processo não consegue criar os arquivos necessários para iniciar.",
                "hypotheses": ["permissão temporariamente inconsistente"],
                "diagnostic_tools": [
                    {
                        "tool": "systemd.inspect_unit",
                        "arguments": {"unit": "check-mk-agent.socket"},
                        "purpose": "confirmar o estado após a falha",
                    }
                ],
                "stop": False,
                "stop_reason": "",
            }, {"success": True, "purpose": purpose}
        return {
            "root_blocker": "falha transitória de permissão já normalizada",
            "causal_chain": [
                "permissão temporária inconsistente",
                "falha de inicialização",
                "automation-helper parado",
            ],
            "next_action": {
                "description": "Tentar novamente a recuperação já autorizada",
                "tool": "checkmk.recover_omd_service",
                "arguments": _action()["arguments"],
                "evidence_reason": "A validação de leitura indica que o bloqueio não persiste.",
            },
            "requires_new_approval": False,
            "resolved": False,
            "stop": False,
            "stop_reason": "",
        }, {"success": True, "purpose": purpose}

    monkeypatch.setattr(recovery_loop, "execute_tool", fake_execute)
    monkeypatch.setattr(recovery_loop, "resilient_model_call", fake_model_call)
    monkeypatch.setattr(
        recovery_loop,
        "review_corrections",
        lambda *args, **kwargs: {"approved": True, "reason": "ação segura e sustentada"},
    )

    result = run_adaptive_recovery(
        executor=DummyExecutor(),
        environment=EnvironmentType.MONITORING,
        initial_actions=[_action()],
        analysis={"probable_cause": "falha de criação no diretório temporário"},
        evidence=[{"tool": "journal.read_unit", "status": "executed", "exit_code": 0}],
        scope=_scope("checkmk.recover_omd_service"),
        settings=_settings(),
    )

    assert result["status"] == "validated"
    assert result["state"] == "resolved_and_validated"
    assert len(result["results"]) == 2
    assert len(result["diagnostic_results"]) == 1
    assert result["blockers"][0]["summary"] == "Permissão negada no diretório temporário"
    assert result["results"][1]["adaptive"] is True


def test_action_outside_approved_envelope_is_not_executed(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute(_executor, _environment, tool, arguments, *, approved=False):
        calls.append(tool)
        return {
            "tool": tool,
            "arguments": arguments,
            "status": "failed",
            "exit_code": 1,
            "stdout": "",
            "stderr": "dependency failed",
        }

    def fake_model_call(_prompt, purpose, provider_name=None):
        del provider_name
        if purpose.startswith("recovery_diagnosis"):
            return {
                "blocker_summary": "Outra unidade precisa ser recuperada",
                "new_symptom": "dependency failed",
                "causal_link": "A dependência impede a inicialização.",
                "hypotheses": [],
                "diagnostic_tools": [],
                "stop": False,
                "stop_reason": "",
            }, {"success": True}
        return {
            "root_blocker": "dependência systemd parada",
            "causal_chain": ["dependência parada", "automation-helper parado"],
            "next_action": {
                "description": "Recuperar dependência",
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "check-mk-agent.socket", "action": "start"},
                "evidence_reason": "A dependência está inactive.",
            },
            "requires_new_approval": True,
            "resolved": False,
            "stop": False,
            "stop_reason": "",
        }, {"success": True}

    monkeypatch.setattr(recovery_loop, "execute_tool", fake_execute)
    monkeypatch.setattr(recovery_loop, "resilient_model_call", fake_model_call)

    result = run_adaptive_recovery(
        executor=DummyExecutor(),
        environment=EnvironmentType.MONITORING,
        initial_actions=[_action()],
        analysis={"probable_cause": "dependência indisponível"},
        evidence=[],
        scope=_scope("checkmk.recover_omd_service"),
        settings=_settings(),
    )

    assert result["status"] == "approval_required"
    assert result["state"] == "awaiting_new_approval"
    assert calls == ["checkmk.recover_omd_service"]
    assert result["pending_actions"][0]["tool"] == "systemd.recover_unit"
    assert result["pending_actions"][0]["status"] == "new_approval_required"


def test_repeated_failed_action_stops_loop(monkeypatch) -> None:
    monkeypatch.setattr(
        recovery_loop,
        "execute_tool",
        lambda *args, **kwargs: {
            "tool": args[2],
            "status": "failed",
            "exit_code": 1,
            "stdout": "",
            "stderr": "same failure",
        },
    )

    def fake_model_call(_prompt, purpose, provider_name=None):
        del provider_name
        if purpose.startswith("recovery_diagnosis"):
            return {
                "blocker_summary": "Falha permanece",
                "new_symptom": "same failure",
                "causal_link": "A ação não removeu o bloqueio.",
                "hypotheses": [],
                "diagnostic_tools": [],
                "stop": False,
                "stop_reason": "",
            }, {"success": True}
        return {
            "root_blocker": "bloqueio persistente",
            "causal_chain": [],
            "next_action": {**_action(), "status": "proposed"},
            "requires_new_approval": False,
            "resolved": False,
            "stop": False,
            "stop_reason": "",
        }, {"success": True}

    monkeypatch.setattr(recovery_loop, "resilient_model_call", fake_model_call)
    monkeypatch.setattr(
        recovery_loop,
        "review_corrections",
        lambda *args, **kwargs: {"approved": True, "reason": "teste"},
    )

    result = run_adaptive_recovery(
        executor=DummyExecutor(),
        environment=EnvironmentType.MONITORING,
        initial_actions=[_action()],
        analysis={"probable_cause": "bloqueio persistente"},
        evidence=[],
        scope=_scope("checkmk.recover_omd_service"),
        settings=_settings(agent_recovery_max_rounds=5),
    )

    assert result["state"] == "stopped_loop_detected"
    assert result["status"] == "failed"
    assert any("mesma ação" in item["summary"] for item in result["blockers"])


def test_correction_tool_cannot_be_used_as_diagnostic(monkeypatch) -> None:
    monkeypatch.setattr(
        recovery_loop,
        "execute_tool",
        lambda *args, **kwargs: {
            "tool": args[2],
            "status": "failed",
            "exit_code": 1,
            "stdout": "",
            "stderr": "failed",
        },
    )

    def fake_model_call(_prompt, purpose, provider_name=None):
        del provider_name
        if purpose.startswith("recovery_diagnosis"):
            return {
                "blocker_summary": "Falha desconhecida",
                "new_symptom": "failed",
                "causal_link": "bloqueio",
                "hypotheses": [],
                "diagnostic_tools": [
                    {
                        "tool": "systemd.recover_unit",
                        "arguments": {"unit": "xinetd", "action": "start"},
                        "purpose": "tentar corrigir durante diagnóstico",
                    }
                ],
                "stop": False,
                "stop_reason": "",
            }, {"success": True}
        return {
            "root_blocker": "sem evidência segura",
            "causal_chain": [],
            "next_action": None,
            "requires_new_approval": False,
            "resolved": False,
            "stop": True,
            "stop_reason": "nenhum caminho seguro",
        }, {"success": True}

    monkeypatch.setattr(recovery_loop, "resilient_model_call", fake_model_call)

    result = run_adaptive_recovery(
        executor=DummyExecutor(),
        environment=EnvironmentType.MONITORING,
        initial_actions=[_action()],
        analysis={"probable_cause": "desconhecida"},
        evidence=[],
        scope=_scope("checkmk.recover_omd_service"),
        settings=_settings(),
    )

    diagnostic = result["diagnostic_results"][0]
    assert diagnostic["tool"] == "systemd.recover_unit"
    assert diagnostic["status"] == "blocked"
    assert "leitura" in diagnostic["reason"]
