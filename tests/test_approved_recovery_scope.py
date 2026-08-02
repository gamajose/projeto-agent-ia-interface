from __future__ import annotations

from app.services.approved_execution import _pending_tools_belong_to_playbook


def test_pending_action_must_belong_to_recovery_scope() -> None:
    analysis = {
        "recovery_scope": {
            "allowed_correction_tools": ["checkmk.recover_omd_service"]
        }
    }

    assert _pending_tools_belong_to_playbook(
        analysis,
        [{"tool": "checkmk.recover_omd_service", "arguments": {}}],
    ) is True
    assert _pending_tools_belong_to_playbook(
        analysis,
        [{"tool": "systemd.recover_unit", "arguments": {}}],
    ) is False


def test_empty_or_unmapped_pending_action_cannot_receive_new_token() -> None:
    assert _pending_tools_belong_to_playbook({}, []) is False
    assert _pending_tools_belong_to_playbook(
        {"recovery_scope": {"allowed_correction_tools": []}},
        [{"tool": "systemd.recover_unit"}],
    ) is False
