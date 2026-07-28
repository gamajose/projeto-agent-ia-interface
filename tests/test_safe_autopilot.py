from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("POSTGRES_DSN", "sqlite+pysqlite:///:memory:")

from app.core.policies import EnvironmentType
from app.core.settings import Settings
from app.services.ai_providers import ProviderError
from app.services.provider_preflight import ProviderPreflight, ProviderState
from app.services.provider_router import (
    ProviderResolution,
    automatic_provider_order,
    resolve_automatic_provider,
)
from app.services import provider_router, runner
from app.web import InvestigationPayload, _validate_selection


def _settings(**overrides) -> Settings:
    values = {
        "postgres_dsn": "sqlite+pysqlite:///:memory:",
        "agent_autopilot_enabled": True,
        "agent_autopilot_default": True,
        "ai_auto_provider_order": "groq,omniroute,gemini,ollama,openrouter",
        "ssh_strict_host_key_checking": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _row(
    provider: str,
    *,
    selectable: bool,
    model: str = "modelo",
    detail: str = "diagnóstico",
) -> ProviderPreflight:
    return ProviderPreflight(
        provider=provider,
        label={
            "gemini": "Google Gemini",
            "groq": "Groq (Llama)",
            "openrouter": "OpenRouter",
            "ollama": "Ollama local",
            "omniroute": "OmniRoute",
        }[provider],
        state=ProviderState.AVAILABLE if selectable else ProviderState.UNAVAILABLE,
        model=model,
        detail=detail,
        selectable=selectable,
    )


def test_automatic_provider_order_is_configurable_and_deduplicated() -> None:
    settings = _settings(ai_auto_provider_order="omniroute,groq,omniroute")

    order = automatic_provider_order(settings)

    assert order[:2] == ("omniroute", "groq")
    assert len(order) == len(set(order))
    assert set(order) == {"gemini", "groq", "openrouter", "ollama", "omniroute"}


def test_autopilot_falls_back_to_next_fully_validated_provider(monkeypatch) -> None:
    settings = _settings()
    quick = [
        _row("groq", selectable=True, model="llama"),
        _row("omniroute", selectable=True, model="auto/coding"),
        _row("gemini", selectable=False),
        _row("ollama", selectable=False),
        _row("openrouter", selectable=False),
    ]
    monkeypatch.setattr(provider_router, "preflight_all", lambda *args, **kwargs: quick)

    def full(provider: str, *args, **kwargs) -> ProviderPreflight:
        if provider == "groq":
            return _row("groq", selectable=False, model="llama", detail="HTTP 503")
        return _row("omniroute", selectable=True, model="auto/coding", detail="validado")

    monkeypatch.setattr(provider_router, "preflight_provider", full)

    result = resolve_automatic_provider(settings)

    assert result.provider == "omniroute"
    assert result.model == "auto/coding"
    assert result.automatic is True
    full_attempts = [item for item in result.attempts if item["phase"] == "full_preflight"]
    assert [item["provider"] for item in full_attempts] == ["groq", "omniroute"]


def test_autopilot_blocks_before_ssh_when_no_provider_is_healthy(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(
        provider_router,
        "preflight_all",
        lambda *args, **kwargs: [
            _row(provider, selectable=False, detail="indisponível")
            for provider in ("gemini", "groq", "openrouter", "ollama", "omniroute")
        ],
    )

    with pytest.raises(ProviderError, match="Nenhuma IA passou"):
        resolve_automatic_provider(settings)


def test_web_payload_accepts_automatic_provider() -> None:
    payload = InvestigationPayload(
        target="192.0.2.10",
        objective="Investigar alerta automaticamente.",
        provider="auto",
        mode="correct",
        playbook_mode="manual",
        playbook_id="checkmk-agent-port",
    )

    assert payload.provider == "auto"


def test_web_autopilot_forces_proposal_and_automatic_playbook(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(
        "app.web.preflight_all",
        lambda *args, **kwargs: [_row("groq", selectable=True, model="llama")],
    )
    payload = InvestigationPayload(
        target="192.0.2.10",
        objective="Investigar alerta automaticamente.",
        provider="auto",
        mode="correct",
        playbook_mode="manual",
        playbook_id="checkmk-agent-port",
    )

    provider, model, mode = _validate_selection(payload, settings)

    assert provider == "auto"
    assert model is None
    assert mode == "propose"


def test_runner_autopilot_accesses_analyzes_and_never_corrects_without_approval(monkeypatch) -> None:
    settings = _settings(ssh_bastion_host="192.0.2.1")
    captured: dict[str, object] = {}
    selection = ProviderResolution(
        provider="groq",
        model="llama-test",
        label="Groq (Llama)",
        detail="selecionado automaticamente",
        automatic=True,
        attempts=(),
    )
    monkeypatch.setattr(runner, "resolve_automatic_provider", lambda settings: selection)
    monkeypatch.setattr(runner, "selected_playbook_ssh_port", lambda objective: (None, None))
    monkeypatch.setattr(
        runner,
        "resolve_target",
        lambda *args, **kwargs: runner.ResolvedTarget(
            reference="192.0.2.10",
            host="192.0.2.10",
            port=22,
            environment=EnvironmentType.UNKNOWN,
            inventory=None,
        ),
    )

    class FakeExecutor:
        port = 22

        def connect(self) -> None:
            captured["connected"] = True

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(runner, "build_executor", lambda *args, **kwargs: FakeExecutor())

    def investigate(**kwargs):
        captured["mode"] = kwargs["mode"]
        captured["approve"] = kwargs["approve"]
        return {
            "investigation_id": "test-id",
            "target": "192.0.2.10",
            "hostname": "host-test",
            "environment_classification": {"environment": "production"},
            "playbook": {"id": "linux-health", "title": "Linux health"},
            "evidence": [{"status": "executed"}],
            "analysis": {
                "status": "attention",
                "proposed_actions": [{"description": "Ação segura", "status": "proposed"}],
            },
            "approval_token": "token-humano",
        }

    monkeypatch.setattr(runner, "run_dynamic_investigation", investigate)

    result = runner.run_target(
        "192.0.2.10",
        "Investigar automaticamente",
        provider_name="auto",
        mode="correct",
        approve=True,
        playbook_mode="manual",
        playbook_id="qualquer",
        settings=settings,
    )

    assert captured == {
        "connected": True,
        "mode": "propose",
        "approve": False,
        "closed": True,
    }
    assert result["selected_provider"] == "groq"
    assert result["automation"]["mode"] == "safe_autopilot"
    assert result["automation"]["safety"]["production_changes"] == "blocked"
    assert result["automation"]["human_approval_available"] is True
    correction_phase = result["automation"]["phases"][-1]
    assert correction_phase["status"] == "approval_required"
