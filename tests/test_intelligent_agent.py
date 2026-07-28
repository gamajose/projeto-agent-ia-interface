from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import intelligent_agent
from app.services.ai_providers import use_provider


class FakeProvider:
    def __init__(self, name: str, model: str, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.name = name
        self.model = model
        self._result = result
        self._error = error

    def generate_json(self, prompt: str):
        if self._error:
            raise self._error
        return dict(self._result or {}), {"response_chars": len(prompt)}


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "ai_provider": "groq",
        "ai_reviewer_provider": "groq",
        "agent_reasoning_provider_fallback": True,
        "agent_reasoning_max_provider_attempts": 3,
        "agent_intelligent_reasoning_enabled": True,
        "agent_playbook_advisory_only": True,
        "agent_critic_enabled": True,
        "agent_critic_min_coverage": 70,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reasoning_contract_rejects_invalid_planner_output() -> None:
    with pytest.raises(ValueError, match="tools"):
        intelligent_agent._validate_reasoning_output(
            "planning_round_1",
            {"hypotheses": [], "tools": "não é lista", "done": False, "confidence": 10},
        )


def test_reasoning_contract_accepts_valid_final_analysis() -> None:
    intelligent_agent._validate_reasoning_output(
        "final_analysis",
        {
            "status": "attention",
            "confidence": 82,
            "summary": "Serviço sem listener.",
            "facts": ["Porta ausente."],
            "evidence_map": [{"conclusion": "porta ausente", "command": "ss", "evidence": "sem retorno"}],
        },
    )


def test_model_call_fails_over_when_selected_provider_fails(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(intelligent_agent, "get_settings", lambda: settings)
    monkeypatch.setattr(
        intelligent_agent,
        "automatic_provider_order",
        lambda _settings: ("groq", "omniroute", "gemini"),
    )
    monkeypatch.setattr(
        intelligent_agent,
        "preflight_provider",
        lambda name, *_args, **_kwargs: SimpleNamespace(
            selectable=True,
            model="auto/coding" if name == "omniroute" else "modelo",
            detail="ok",
        ),
    )

    def provider(name: str, _settings: Any, model: str | None):
        if name == "groq":
            return FakeProvider("groq", model or "llama", error=RuntimeError("temporário"))
        return FakeProvider(
            "omniroute",
            model or "auto/coding",
            result={
                "mission": "Investigar comunicação",
                "success_criteria": ["confirmar listener"],
                "unknowns": ["estado da porta"],
                "stop_conditions": ["causa comprovada"],
                "initial_confidence": 10,
            },
        )

    monkeypatch.setattr(intelligent_agent, "get_provider", provider)

    with use_provider("groq", "llama"):
        result, diagnostics = intelligent_agent.resilient_model_call(
            "objetivo",
            "mission_interpretation",
        )

    assert result is not None
    assert result["_ai_provider"] == "omniroute"
    assert diagnostics["success"] is True
    assert diagnostics["failover_used"] is True
    assert [item["status"] for item in diagnostics["attempts"]] == ["failed", "success"]


def test_playbook_is_advisory_during_intelligent_session(monkeypatch) -> None:
    monkeypatch.setattr(
        intelligent_agent,
        "get_settings",
        lambda: _settings(agent_playbook_advisory_only=True),
    )
    token = intelligent_agent._INTELLIGENT_SESSION.set(True)
    try:
        assert intelligent_agent._render_steps_advisory(object(), {}) == []
    finally:
        intelligent_agent._INTELLIGENT_SESSION.reset(token)


def test_critic_rejects_unsupported_conclusion_and_removes_approval() -> None:
    result = {
        "approval_token": "token-secreto",
        "analysis": {
            "status": "critical",
            "confidence": 92,
            "conclusion": "Serviço definitivamente parado.",
            "recommendations": [],
            "approval": {"required": True},
            "proposed_actions": [{"tool": "systemd.recover_unit", "status": "proposed"}],
        },
        "review": {"approved": True},
    }
    critic = {
        "verdict": "insufficient",
        "evidence_coverage": 45,
        "confidence": 40,
        "safe_to_propose": False,
        "summary": "Não houve coleta do estado da unidade.",
        "unsupported_claims": ["serviço definitivamente parado"],
        "missing_evidence": ["systemctl show da unidade"],
    }

    intelligent_agent._apply_critic(result, critic, _settings())

    assert result["approval_token"] is None
    assert result["analysis"]["status"] == "inconclusive"
    assert result["analysis"]["confidence"] == 40
    assert "approval" not in result["analysis"]
    assert result["analysis"]["proposed_actions"][0]["status"] == "critic_rejected"
    assert result["review"]["approved"] is False


def test_wrapper_adds_mission_and_independent_critic(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(intelligent_agent, "get_settings", lambda: settings)
    monkeypatch.setattr(
        intelligent_agent,
        "interpret_mission",
        lambda objective: (
            {
                "mission": f"Validar {objective}",
                "success_criteria": ["evidência atual"],
                "unknowns": ["causa"],
                "stop_conditions": ["causa comprovada"],
                "initial_confidence": 0,
            },
            {"purpose": "mission_interpretation", "success": True},
        ),
    )
    captured: dict[str, Any] = {}

    def fake_engine(**kwargs: Any) -> dict[str, Any]:
        captured["context"] = kwargs["context"]
        return {
            "investigation_id": "abc",
            "context": kwargs["context"],
            "analysis": {
                "status": "attention",
                "confidence": 80,
                "summary": "Falha confirmada.",
                "facts": ["listener ausente"],
                "conclusion": "Serviço sem listener.",
                "recommendations": [],
                "proposed_actions": [],
            },
            "evidence": [{"tool": "network.listeners", "status": "executed", "stdout": ""}],
            "round_assessments": [],
            "deterministic_signals": [],
            "ai_diagnostics": [],
            "approval_token": None,
        }

    monkeypatch.setattr(intelligent_agent.engine, "run_dynamic_investigation", fake_engine)
    monkeypatch.setattr(
        intelligent_agent,
        "critique_result",
        lambda **_kwargs: (
            {
                "verdict": "accept",
                "evidence_coverage": 90,
                "confidence": 78,
                "safe_to_propose": True,
                "supported_claims": ["listener ausente"],
                "unsupported_claims": [],
                "contradictions": [],
                "missing_evidence": [],
                "summary": "Conclusão sustentada.",
            },
            {"purpose": "final_critic", "success": True},
        ),
    )
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(
        intelligent_agent,
        "update_investigation_analysis",
        lambda investigation_id, analysis: persisted.update({"id": investigation_id, "analysis": analysis}),
    )

    result = intelligent_agent.run_dynamic_investigation(
        executor=object(),
        target="192.0.2.10",
        context="porta 6556 sem comunicação",
        environment=object(),
        mode="propose",
        approve=False,
    )

    assert "MISSÃO E CRITÉRIOS" in captured["context"]
    assert result["context"] == "porta 6556 sem comunicação"
    assert result["intelligence"]["loop"] == "understand-plan-act-observe-reflect-replan-critic"
    assert result["intelligence"]["critic"]["verdict"] == "accept"
    assert result["analysis"]["confidence"] == 78
    assert persisted["id"] == "abc"
