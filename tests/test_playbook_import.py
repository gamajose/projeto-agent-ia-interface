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


def test_preview_imported_playbook_rejects_corrections_and_validation() -> None:
    base = {
        "id": "playbook-seguro",
        "title": "Playbook seguro",
        "profiles": ["linux_generic"],
        "match": {"any": ["teste"]},
        "steps": [{"tool": "system.basics", "arguments": {}, "purpose": "Identificar"}],
    }

    with pytest.raises(ValueError, match="somente playbooks de leitura"):
        preview_imported_playbook(yaml.safe_dump({**base, "allowed_corrections": ["systemd.recover_unit"]}))

    with pytest.raises(ValueError, match="não aceita pós-validações"):
        preview_imported_playbook(yaml.safe_dump({**base, "validation": [{"tool": "system.basics"}]}))
