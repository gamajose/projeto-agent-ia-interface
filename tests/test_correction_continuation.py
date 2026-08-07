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
        "objective": "O check-mk-agent está parado",
        "evidence": [],
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


def _patch_persistence(monkeypatch, item: dict) -> dict:
    saved: dict = {}
    monkeypatch.setattr(correction_continuation, "get_investigation", lambda *args, **kwargs: item)
    monkeypatch.setattr(
        correction_continuation,
        "update_investigation_analysis",
        lambda investigation_id, analysis: saved.update(investigation_id=investigation_id, analysis=analysis),
    )
    # Estes são testes unitários do fluxo de continuação. A resolução do inventário
    # é testada em persistence; aqui não devemos depender do schema PostgreSQL do CI.
    monkeypatch.setattr(
        correction_continuation,
        "resolve_saved_target",
        lambda target, environment: {"vpn_ip": target, "ssh_port": 2222},
    )
    return saved


def _patch_token(monkeypatch, token: str = "token.assinado") -> dict:
    captured: dict = {}

    def fake_token(investigation_id, target, actions, **kwargs):
        captured.update(
            investigation_id=investigation_id,
            target=target,
            actions=actions,
            ssh_port=kwargs.get("ssh_port"),
        )
        return token

    monkeypatch.setattr(correction_continuation, "create_approval_token", fake_token)
    return captured


def test_prepare_continuation_reuses_validated_proposal_without_reanalysis(monkeypatch) -> None:
    saved = _patch_persistence(monkeypatch, _investigation())
    captured = _patch_token(monkeypatch)

    result = correction_continuation.prepare_correction_continuation(
        "00000000-0000-0000-0000-000000000001",
        settings=_settings(),
    )

    assert result["approval_token"] == "token.assinado"
    assert result["can_execute"] is True
    assert result["actions_count"] == 1
    assert result["correction_readiness"]["host_restart"]["status"] == "not_required"
    assert captured["ssh_port"] == 2222
    assert captured["target"] == "192.0.2.10"
    assert saved["analysis"]["correction_request"]["state"] == "prepared"


@pytest.mark.parametrize("environment", ["production", "standby"])
def test_prepare_continuation_allows_reviewed_safe_action_in_protected_environment(monkeypatch, environment: str) -> None:
    _patch_persistence(monkeypatch, _investigation(environment))
    captured = _patch_token(monkeypatch)

    result = correction_continuation.prepare_correction_continuation(
        "00000000-0000-0000-0000-000000000001",
        settings=_settings(),
    )

    assert result["can_execute"] is True
    assert result["approval_token"] == "token.assinado"
    assert result["actions_count"] == 1
    assert result["environment"] == environment
    assert result["correction_readiness"]["environment"] == environment
    assert captured["target"] == "192.0.2.10"


def test_prepare_continuation_keeps_unknown_environment_proposal_only(monkeypatch) -> None:
    _patch_persistence(monkeypatch, _investigation("unknown"))

    result = correction_continuation.prepare_correction_continuation(
        "00000000-0000-0000-0000-000000000001",
        settings=_settings(),
    )

    assert result["can_execute"] is False
    assert result["approval_token"] is None
    assert result["actions_count"] == 1
    assert "unknown" in result["reason"]
    assert result["correction_readiness"]["environment"] == "unknown"


def test_prepare_continuation_returns_reason_when_no_action_exists(monkeypatch) -> None:
    item = _investigation()
    item["analysis"]["proposed_actions"] = []
    _patch_persistence(monkeypatch, item)

    result = correction_continuation.prepare_correction_continuation(
        "00000000-0000-0000-0000-000000000001",
        settings=_settings(),
    )

    assert result["can_execute"] is False
    assert result["actions"] == []
    assert "playbook" in result["reason"]


def test_prepare_continuation_returns_reason_when_second_ai_declines(monkeypatch) -> None:
    item = _investigation()
    item["analysis"]["review"]["approved"] = False
    _patch_persistence(monkeypatch, item)

    result = correction_continuation.prepare_correction_continuation(
        "00000000-0000-0000-0000-000000000001",
        settings=_settings(),
    )

    assert result["can_execute"] is False
    assert "segunda IA" in result["reason"]
