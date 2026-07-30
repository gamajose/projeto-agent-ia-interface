from __future__ import annotations

import pytest
import yaml

from app.services.playbook_import import preview_imported_playbook


def test_preview_imported_playbook_returns_reviewable_safe_draft() -> None:
    content = yaml.safe_dump(
        {
            "id": "diagnostico-swap-importado",
            "title": "Diagnóstico de swap importado",
            "priority": 25,
            "profiles": ["linux_generic"],
            "match": {"any": ["swap", "mem[oó]ria"]},
            "steps": [
                {"tool": "system.basics", "arguments": {}, "purpose": "Identificar host"},
                {"tool": "memory.swap", "arguments": {}, "purpose": "Validar swap"},
            ],
            "allowed_corrections": [],
            "validation": [],
        },
        allow_unicode=True,
        sort_keys=False,
    )

    draft = preview_imported_playbook(content, filename="meu-playbook.yml")

    assert draft["id"] == "diagnostico-swap-importado"
    assert draft["title"] == "Diagnóstico de swap importado"
    assert draft["profiles"] == ["linux_generic"]
    assert draft["patterns"] == ["swap", "mem[oó]ria"]
    assert draft["source_filename"] == "meu-playbook.yml"
    assert draft["import_warnings"] == []
    steps = yaml.safe_load(draft["steps_yaml"])
    assert [item["tool"] for item in steps] == ["system.basics", "memory.swap"]


def test_preview_imported_playbook_rejects_shell_commands() -> None:
    content = yaml.safe_dump(
        {
            "id": "playbook-shell",
            "title": "Playbook com shell",
            "profiles": ["linux_generic"],
            "match": {"any": ["teste"]},
            "steps": [{"command": "reboot", "purpose": "não permitido"}],
        },
        sort_keys=False,
    )
    with pytest.raises(ValueError, match="comandos shell"):
        preview_imported_playbook(content)


def test_preview_imported_playbook_removes_corrections_and_validation_for_review() -> None:
    content = yaml.safe_dump(
        {
            "id": "playbook-seguro",
            "title": "Playbook seguro",
            "profiles": "linux_generic",
            "match": {"any": "teste"},
            "steps": [{"tool": "system.basics", "arguments": {}, "purpose": "Identificar"}],
            "allowed_corrections": ["systemd.recover_unit"],
            "validation": [{"tool": "system.basics"}],
        },
        allow_unicode=True,
        sort_keys=False,
    )

    draft = preview_imported_playbook(content)

    assert draft["profiles"] == ["linux_generic"]
    assert draft["patterns"] == ["teste"]
    assert len(draft["import_warnings"]) == 2
    assert "allowed_corrections" in draft["import_warnings"][0]
    assert "validation" in draft["import_warnings"][1]
    assert "systemd.recover_unit" not in draft["steps_yaml"]


def test_preview_imported_playbook_accepts_wrapped_document_and_checks_alias() -> None:
    content = yaml.safe_dump(
        {
            "playbook": {
                "name": "diagnostico-basico",
                "title": "Diagnóstico básico",
                "profiles": ["linux_generic"],
                "patterns": ["saúde geral"],
                "checks": [
                    {"tool": "system.basics", "arguments": {}, "purpose": "Identificar host"},
                ],
            }
        },
        allow_unicode=True,
        sort_keys=False,
    )

    draft = preview_imported_playbook(content, filename="embrulhado.yaml")

    assert draft["id"] == "diagnostico-basico"
    assert draft["patterns"] == ["saúde geral"]
    assert yaml.safe_load(draft["steps_yaml"])[0]["tool"] == "system.basics"


def test_preview_imported_playbook_rejects_multiple_documents() -> None:
    content = "id: primeiro\ntitle: Primeiro playbook\nsteps: []\n---\nid: segundo\ntitle: Segundo playbook\nsteps: []\n"
    with pytest.raises(ValueError, match="um playbook por arquivo"):
        preview_imported_playbook(content)
