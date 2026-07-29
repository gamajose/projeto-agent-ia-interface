from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.settings import Settings
from app.services.playbook_editor import draft_playbook, save_playbook
from app.services.playbooks import list_playbooks, load_playbooks, reload_playbooks


@pytest.fixture(autouse=True)
def clear_playbook_cache():
    load_playbooks.cache_clear()
    yield
    load_playbooks.cache_clear()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        postgres_dsn="sqlite+pysqlite:///:memory:",
        agent_playbook_dir=str(tmp_path / "playbooks"),
    )


def test_save_playbook_writes_yaml_and_reloads_catalog(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.playbooks.get_settings", lambda: settings)

    item = save_playbook(
        playbook_id="diagnostico-swap",
        title="Diagnóstico de memória e swap",
        priority=30,
        profiles=["linux_generic"],
        patterns=["swap", "mem[oó]ria"],
        steps_yaml=(
            "- tool: system.basics\n"
            "  arguments: {}\n"
            "  purpose: Identificar o host.\n"
            "- tool: memory.swap\n"
            "  arguments: {}\n"
            "  purpose: Validar RAM e swap.\n"
        ),
        settings=settings,
    )

    path = Path(settings.agent_playbook_dir) / "diagnostico-swap.yml"
    assert path.exists()
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["id"] == "diagnostico-swap"
    assert payload["allowed_corrections"] == []
    assert [step["tool"] for step in payload["steps"]] == ["system.basics", "memory.swap"]
    assert item["steps_count"] == 2

    reload_playbooks()
    catalog = {playbook.id: playbook for playbook in list_playbooks()}
    assert catalog["diagnostico-swap"].title == "Diagnóstico de memória e swap"


def test_save_playbook_rejects_shell_commands(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.playbooks.get_settings", lambda: settings)

    with pytest.raises(ValueError, match="comandos shell"):
        save_playbook(
            playbook_id="shell-invalido",
            title="Playbook inválido",
            priority=10,
            profiles=["linux_generic"],
            patterns=["teste"],
            steps_yaml="- command: reboot\n  purpose: inválido\n",
            settings=settings,
        )


def test_save_playbook_rejects_correction_tools(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.playbooks.get_settings", lambda: settings)

    with pytest.raises(ValueError, match="corretivas"):
        save_playbook(
            playbook_id="correcao-invalida",
            title="Correção inválida",
            priority=10,
            profiles=["linux_generic"],
            patterns=["systemd"],
            steps_yaml=(
                "- tool: systemd.recover_unit\n"
                "  arguments:\n"
                "    unit: check-mk-agent.socket\n"
                "    action: restart\n"
            ),
            settings=settings,
        )


def test_draft_playbook_uses_safe_tools_from_investigation() -> None:
    draft = draft_playbook(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "objective": "Investigar utilização elevada de memória e swap",
            "profile": "linux_generic",
            "plans": [
                {
                    "tools": [
                        {"tool": "system.basics", "arguments": {}, "purpose": "Identificar host"},
                        {"tool": "memory.swap", "arguments": {}, "purpose": "Validar swap"},
                        {"tool": "systemd.recover_unit", "arguments": {"unit": "x", "action": "restart"}},
                    ]
                }
            ],
        }
    )

    assert draft["id"].startswith("linux-generic-")
    assert draft["profiles"] == ["linux_generic"]
    steps = yaml.safe_load(draft["steps_yaml"])
    assert [item["tool"] for item in steps] == ["system.basics", "memory.swap"]


def test_draft_playbook_is_not_offered_when_one_was_used() -> None:
    with pytest.raises(ValueError, match="já utilizou"):
        draft_playbook(
            {
                "objective": "Validar agente",
                "profile": "checkmk",
                "plans": [{"playbook": {"id": "checkmk-agent"}}],
            }
        )
