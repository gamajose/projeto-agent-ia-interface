from __future__ import annotations

from types import SimpleNamespace

from app.services import noc_requested_correction


def _settings():
    return SimpleNamespace(
        noc_self_heal_tools="systemd.recover_unit,checkmk.recover_omd_service",
        noc_self_heal_min_confidence=85,
    )


def _result(environment: str = "standby") -> dict:
    return {
        "environment_classification": {"environment": environment, "confidence": 96},
        "approval_token": "token",
        "investigation_id": "inv-1",
        "analysis": {
            "confidence": 97,
            "review": {"approved": True},
            "proposed_actions": [
                {
                    "status": "proposed",
                    "tool": "systemd.recover_unit",
                    "arguments": {"unit": "check-mk-agent.socket", "action": "start"},
                }
            ],
            "recovery_scope": {"allowed_correction_tools": ["systemd.recover_unit"]},
        },
    }


def test_manual_arrumar_allows_safe_playbook_correction_in_standby(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_requested_correction,
        "policy_allows_autonomous_correction",
        lambda _incident: (True, "monitoring_sensor", "categoria habilitada"),
    )
    eligible, reason, allowed = noc_requested_correction._eligibility(
        {"service": "Systemd Socket Summary", "manual_correction_requested": True},
        _result("standby"),
        source="manual_selected",
        settings=_settings(),
    )

    assert eligible is True
    assert reason == "correção segura autorizada"
    assert "systemd.recover_unit" in allowed


def test_automatic_correction_requires_trusted_environment_classification(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_requested_correction,
        "policy_allows_autonomous_correction",
        lambda _incident: (True, "monitoring_sensor", "categoria habilitada"),
    )
    result = _result("production")
    result["environment_classification"]["confidence"] = 70

    eligible, reason, _allowed = noc_requested_correction._eligibility(
        {"service": "Systemd Socket Summary"},
        result,
        source="automatic",
        settings=_settings(),
    )

    assert eligible is False
    assert "90%" in reason


def test_manual_unknown_environment_remains_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_requested_correction,
        "policy_allows_autonomous_correction",
        lambda _incident: (True, "monitoring_sensor", "categoria habilitada"),
    )

    eligible, reason, _allowed = noc_requested_correction._eligibility(
        {"service": "Systemd Socket Summary", "manual_correction_requested": True},
        _result("unknown"),
        source="manual_selected",
        settings=_settings(),
    )

    assert eligible is False
    assert "unknown" in reason


def test_selected_runner_marks_arrumar_as_correction_intent() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    runner = (root / "app" / "services" / "noc_selected_runner.py").read_text(encoding="utf-8")
    hooks = (root / "app" / "services" / "noc_worker_hooks.py").read_text(encoding="utf-8")

    assert "manual_correction_requested" in runner
    assert "INTENÇÃO DO OPERADOR: ARRUMAR ESTE PROBLEMA" in runner
    assert "attempt_requested_correction" in hooks
