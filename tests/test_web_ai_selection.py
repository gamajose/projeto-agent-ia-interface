from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("POSTGRES_DSN", "sqlite+pysqlite:///:memory:")

from app.core.policies import EnvironmentType
from app.services import jobs, runner
from app.services.ai_providers import current_model_override, current_provider_override
from app.services.playbooks import current_playbook_selection
from app.web import InvestigationPayload


def test_web_payload_accepts_provider_model_and_playbook_selection() -> None:
    payload = InvestigationPayload(
        target="192.0.2.10",
        objective="Investigar a porta do agente Checkmk.",
        environment=EnvironmentType.MONITORING,
        mode="correct",
        provider="groq",
        model="llama-test",
        playbook_mode="manual",
        playbook_id="checkmk-agent-port",
    )

    assert payload.provider == "groq"
    assert payload.model == "llama-test"
    assert payload.playbook_mode == "manual"
    assert payload.mode == "correct"


def test_web_payload_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        InvestigationPayload(
            target="192.0.2.10",
            objective="Investigar alerta.",
            provider="provedor-inexistente",
        )


def test_runner_applies_provider_and_playbook_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_preflight(settings: object) -> object:
        captured["provider"] = current_provider_override()
        captured["model"] = current_model_override()
        return object()

    def fake_playbook_port(objective: str) -> tuple[None, None]:
        captured["playbook"] = current_playbook_selection()
        return None, None

    class FakeExecutor:
        port = 22

        def connect(self) -> None:
            captured["connected"] = True

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(runner, "require_selected_provider", fake_preflight)
    monkeypatch.setattr(runner, "selected_playbook_ssh_port", fake_playbook_port)
    monkeypatch.setattr(
        runner,
        "resolve_target",
        lambda *args, **kwargs: runner.ResolvedTarget(
            reference="192.0.2.10",
            host="192.0.2.10",
            port=22,
            environment=EnvironmentType.MONITORING,
            inventory=None,
        ),
    )
    monkeypatch.setattr(runner, "build_executor", lambda *args, **kwargs: FakeExecutor())
    monkeypatch.setattr(
        runner,
        "run_dynamic_investigation",
        lambda **kwargs: {"investigation_id": "test", "analysis": {"status": "healthy"}},
    )

    result = runner.run_target(
        "192.0.2.10",
        "Investigar Checkmk",
        provider_name="groq",
        model_name="llama-test",
        playbook_mode="none",
        settings=object(),
    )

    assert result["investigation_id"] == "test"
    assert captured == {
        "provider": "groq",
        "model": "llama-test",
        "playbook": ("none", None),
        "connected": True,
        "closed": True,
    }
    assert current_provider_override() is None
    assert current_playbook_selection() == ("auto", None)


def test_queue_preserves_provider_and_playbook_selection(monkeypatch) -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.pushed: str | None = None

        def setex(self, key: str, ttl: int, value: str) -> None:
            pass

        def rpush(self, queue: str, value: str) -> None:
            self.pushed = value

    fake = FakeRedis()
    monkeypatch.setattr(jobs, "_redis", lambda settings: fake)
    settings = SimpleNamespace(
        redis_url="redis://unused",
        agent_result_prefix="result:",
        agent_job_ttl_seconds=60,
        agent_queue_name="jobs",
        agent_worker_name="test",
        ai_provider="gemini",
    )

    queued = jobs.enqueue_investigation(
        "192.0.2.10",
        "Investigar Checkmk",
        provider_name="openrouter",
        model_name="modelo/teste",
        playbook_mode="manual",
        playbook_id="checkmk-agent-port",
        settings=settings,
    )

    job = json.loads(fake.pushed or "{}")
    assert queued["provider"] == "openrouter"
    assert job["model"] == "modelo/teste"
    assert job["playbook_mode"] == "manual"
    assert job["playbook_id"] == "checkmk-agent-port"
