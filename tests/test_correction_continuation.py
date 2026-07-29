from __future__ import annotations

import pytest

from app.core.settings import Settings
from app.services import correction_continuation


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_dsn="sqlite+pysqlite:///:memory:",
        ssh_default_port=22,
        approval_secret="segredo-de-teste-suficientemente-longo",
        approval_ttl_minutes=10,
    )


def _investigation(environment: str = "monitoring") -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "target": "192.0.2.10",
        "environment": environment,
        "analysis": {
            "review": {"approved": True},
            "critic": {"verdict": "accept", "safe_to_propose": True},
            "proposed_actions": [
                {
                    "status": "proposed",
                    "tool": "systemd.recover_unit",
                    "arguments": {"unit": "check-mk-agent.socket", "action": "start"},
                }
            ],
        },
    }


def test_prepare_continuation_reuses_validated_proposal_without_reanalysis(monkeypatch) -> None:
    monkeypatch.setattr(correction_continuation, "get_investigation", lambda *args, **kwargs: _investigation())
    monkeypatch.setattr(
        correction_continuation,
        "resolve_saved_target",
        lambda *args, **kwargs: {"vpn_ip": "192.0.2.10", "ssh_port": 2222},
    )
    captured = {}

    def fake_token(investigation_id, target, actions, **kwargs):
        captured.update(
            investigation_id=investigation_id,
            target=target,
            actions=actions,
            ssh_port=kwargs.get("ssh_port"),
        )
        return "token.assinado"

    monkeypatch.setattr(correction_continuation, "create_approval_token", fake_token)

    result = correction_continuation.prepare_correction_continuation(
        "00000000-0000-0000-0000-000000000001",
        settings=_settings(),
    )

    assert result["approval_token"] == "token.assinado"
    assert result["actions_count"] == 1
    assert captured["ssh_port"] == 2222
    assert captured["target"] == "192.0.2.10"


@pytest.mark.parametrize("environment", ["production", "standby", "unknown"])
def test_prepare_continuation_blocks_protected_environments(monkeypatch, environment: str) -> None:
    monkeypatch.setattr(
        correction_continuation,
        "get_investigation",
        lambda *args, **kwargs: _investigation(environment),
    )

    with pytest.raises(correction_continuation.CorrectionContinuationError, match="não correção"):
        correction_continuation.prepare_correction_continuation(
            "00000000-0000-0000-0000-000000000001",
            settings=_settings(),
        )


def test_prepare_continuation_requires_proposed_actions(monkeypatch) -> None:
    item = _investigation()
    item["analysis"]["proposed_actions"] = []
    monkeypatch.setattr(correction_continuation, "get_investigation", lambda *args, **kwargs: item)

    with pytest.raises(correction_continuation.CorrectionContinuationError, match="não possui ação corretiva"):
        correction_continuation.prepare_correction_continuation(
            "00000000-0000-0000-0000-000000000001",
            settings=_settings(),
        )


def test_prepare_continuation_requires_second_ai_approval(monkeypatch) -> None:
    item = _investigation()
    item["analysis"]["review"]["approved"] = False
    monkeypatch.setattr(correction_continuation, "get_investigation", lambda *args, **kwargs: item)

    with pytest.raises(correction_continuation.CorrectionContinuationError, match="segunda IA"):
        correction_continuation.prepare_correction_continuation(
            "00000000-0000-0000-0000-000000000001",
            settings=_settings(),
        )
