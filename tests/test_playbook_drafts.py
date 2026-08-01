from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from app.services import playbook_drafts


def _investigation() -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "objective": "Process sf5 automation helpers CRITICAL",
        "profile": "checkmk",
        "analysis": {"probable_cause": "automation-helper parado", "confidence": 92},
        "plans": [
            {
                "tools": [
                    {"tool": "checkmk.find_omd_service", "arguments": {"service": "automation-helper"}, "purpose": "Validar o processo."},
                    {"tool": "docker.inspect_health", "arguments": {"container": "checkmk-sf5-25"}, "purpose": "Validar o healthcheck."},
                ]
            }
        ],
        "evidence": [
            {"tool": "checkmk.find_omd_service", "arguments": {"service": "automation-helper"}, "exit_code": 0},
            {"tool": "docker.inspect_health", "arguments": {"container": "checkmk-sf5-25"}, "exit_code": 0},
        ],
    }


def test_generates_reviewable_yaml_only_after_validated_action(monkeypatch) -> None:
    stored: dict = {}
    monkeypatch.setattr(playbook_drafts, "get_investigation", lambda *_args, **_kwargs: _investigation())
    monkeypatch.setattr(
        playbook_drafts,
        "save_playbook_draft",
        lambda _investigation_id, **kwargs: stored.update(kwargs) or {"id": "draft-1", **kwargs, "status": "draft"},
    )

    draft = playbook_drafts.generate_playbook_draft(
        "11111111-1111-1111-1111-111111111111",
        [{"tool": "checkmk.recover_omd_service", "arguments": {"site": "sf5"}, "status": "validated"}],
        generated_by="jose",
    )

    assert draft is not None
    payload = yaml.safe_load(stored["yaml_content"])
    assert payload["metadata"]["requires_human_review"] is True
    assert payload["metadata"]["validation_is_read_only"] is True
    assert payload["allowed_corrections"] == ["checkmk.recover_omd_service"]
    assert payload["profiles"] == ["checkmk"]
    assert {item["tool"] for item in payload["validation"]} == {
        "checkmk.find_omd_service",
        "docker.inspect_health",
    }
    assert len(stored["yaml_content"].encode("utf-8")) < 100 * 1024


def test_does_not_generate_draft_without_validated_correction() -> None:
    assert playbook_drafts.generate_playbook_draft(
        "11111111-1111-1111-1111-111111111111",
        [{"tool": "checkmk.recover_omd_service", "status": "failed"}],
    ) is None


def test_activation_requires_review_marker_and_writes_inside_catalog(tmp_path: Path, monkeypatch) -> None:
    content = yaml.safe_dump(
        {
            "id": "learned-sf5",
            "title": "Solução SF5",
            "priority": 55,
            "profiles": ["checkmk"],
            "match": {"any": ["automation-helper"]},
            "steps": [],
            "allowed_corrections": ["checkmk.recover_omd_service"],
            "validation": [
                {"tool": "checkmk.find_omd_service", "arguments": {"service": "automation-helper"}}
            ],
            "metadata": {"requires_human_review": True, "validation_is_read_only": True},
        },
        allow_unicode=True,
        sort_keys=False,
    )
    monkeypatch.setattr(
        playbook_drafts,
        "get_playbook_draft",
        lambda _draft_id: {
            "id": "22222222-2222-2222-2222-222222222222",
            "playbook_id": "learned-sf5",
            "title": "Solução SF5",
            "status": "draft",
            "yaml_content": content,
        },
    )
    reviewed: dict = {}
    monkeypatch.setattr(playbook_drafts, "reload_playbooks", lambda: ())
    monkeypatch.setattr(
        playbook_drafts,
        "review_playbook_draft",
        lambda draft_id, **kwargs: reviewed.update({"id": draft_id, **kwargs}) or reviewed,
    )

    result = playbook_drafts.activate_playbook_draft(
        "22222222-2222-2222-2222-222222222222",
        reviewed_by="jose",
        settings=SimpleNamespace(agent_playbook_dir=str(tmp_path)),
    )

    destination = Path(result["activated_path"])
    assert destination.parent == tmp_path.resolve()
    assert destination.read_text(encoding="utf-8") == content
    assert result["status"] == "approved"


def test_activation_rejects_yaml_without_human_review_marker(tmp_path: Path, monkeypatch) -> None:
    content = yaml.safe_dump({"id": "unsafe", "metadata": {}}, sort_keys=False)
    monkeypatch.setattr(
        playbook_drafts,
        "get_playbook_draft",
        lambda _draft_id: {
            "id": "draft",
            "playbook_id": "unsafe",
            "status": "draft",
            "yaml_content": content,
        },
    )

    try:
        playbook_drafts.activate_playbook_draft(
            "draft",
            reviewed_by="jose",
            settings=SimpleNamespace(agent_playbook_dir=str(tmp_path)),
        )
    except ValueError as exc:
        assert "revisão humana" in str(exc)
    else:
        raise AssertionError("rascunho sem revisão humana não pode ser ativado")


def test_activation_rejects_corrective_validation(tmp_path: Path, monkeypatch) -> None:
    content = yaml.safe_dump(
        {
            "id": "unsafe-validation",
            "validation": [{"tool": "checkmk.recover_omd_service", "arguments": {}}],
            "metadata": {"requires_human_review": True, "validation_is_read_only": True},
        },
        sort_keys=False,
    )
    monkeypatch.setattr(
        playbook_drafts,
        "get_playbook_draft",
        lambda _draft_id: {
            "id": "draft",
            "playbook_id": "unsafe-validation",
            "status": "draft",
            "yaml_content": content,
        },
    )

    try:
        playbook_drafts.activate_playbook_draft(
            "draft",
            reviewed_by="jose",
            settings=SimpleNamespace(agent_playbook_dir=str(tmp_path)),
        )
    except ValueError as exc:
        assert "ferramenta corretiva" in str(exc)
    else:
        raise AssertionError("validação corretiva não pode ser ativada")
