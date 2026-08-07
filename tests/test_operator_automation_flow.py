from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from app.core.settings import Settings
from app.services.access_monitors import list_access_monitors, settings_for_access_monitor
from app.services.codex_cli import resolve_codex_command
from app.web import _normalize_range_target
from app.web_projects import _ansible_steps


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_range_checkbox_accepts_three_octet_prefix_as_slash_24() -> None:
    assert _normalize_range_target("172.27.233") == "172.27.233.0/24"
    assert _normalize_range_target("172.27.233.") == "172.27.233.0/24"
    assert _normalize_range_target("172.27.233.45") == "172.27.233.0/24"
    assert _normalize_range_target("172.27.233.0/25") == "172.27.233.0/25"


def test_builtin_access_monitors_come_from_env_fields(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        ssh_bastion_host="10.17.181.1",
        ssh_nuvem="10.17.181.43",
        ssh_cmk05="10.17.181.44",
        access_monitor_registry_path=str(tmp_path / "monitors.json"),
    )
    rows = [item.public_dict() for item in list_access_monitors(settings)]
    assert [(item["label"], item["host"]) for item in rows] == [
        ("Monitor 1", "10.17.181.1"),
        ("Monitor 2", "10.17.181.43"),
        ("Monitor 5", "10.17.181.44"),
    ]


def test_selected_monitor_changes_only_bastion_host_and_reuses_credentials(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        POSTGRES_DSN="postgresql+psycopg://u:p@127.0.0.1/db",
        SSH_SRV_VPN_IP="10.17.181.1",
        SSH_SRV_VPN_PORT=22,
        SSH_SRV_VPN_USER="jose.moraes",
        SSH_SRV_VPN_SENHA="segredo",
        SSH_NUVEM="10.17.181.43",
        SSH_CMK05="10.17.181.44",
        ACCESS_MONITOR_REGISTRY_PATH=str(tmp_path / "monitors.json"),
    )
    monitor2 = settings_for_access_monitor("monitor2", settings)
    monitor5 = settings_for_access_monitor("monitor5", settings)

    assert monitor2.ssh_bastion_host == "10.17.181.43"
    assert monitor5.ssh_bastion_host == "10.17.181.44"
    for selected in (monitor2, monitor5):
        assert selected.ssh_bastion_user == "jose.moraes"
        assert selected.ssh_bastion_password == "segredo"
        assert selected.ssh_bastion_port == 22


def test_project_steps_for_ansible_exclude_manual_changes_and_mark_privileged_reads() -> None:
    plan = {
        "execution_targets": [
            {"reference": "172.27.232.10", "environment": "production"},
        ],
        "groups": [
            {
                "target": "172.27.232.10",
                "kind": "remote",
                "items": [
                    {"title": "SO", "purpose": "SO", "kind": "command", "automated": True, "command": "cat /etc/*-release"},
                    {"title": "Hardware", "purpose": "hardware", "kind": "command", "automated": True, "command": "dmidecode -t1"},
                    {"title": "Instalar", "purpose": "pacotes", "kind": "change", "automated": False, "command": "yum install -y nc"},
                ],
            }
        ],
    }
    steps = _ansible_steps(plan)

    assert [item["command"] for item in steps] == ["cat /etc/*-release", "dmidecode -t1"]
    assert steps[0]["sudo"] is False
    assert steps[1]["sudo"] is True


def test_investigation_ui_is_execution_first_not_command_copying() -> None:
    source = (PROJECT_ROOT / "app/ui/batch.js").read_text(encoding="utf-8")
    projects = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert "Pesquisar por faixa" in source
    assert "/ui/api/access-monitors" in source
    assert "Cadastrar novo servidor" in source
    assert "Comandos executados e retornos" in source
    assert "não é necessário repetir os comandos" in source
    assert "/ui/api/projects/start" in projects
    assert "project-copy-command" not in projects


def test_codex_resolver_accepts_installation_directory(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    os.chmod(executable, 0o755)

    assert resolve_codex_command(str(tmp_path)) == str(executable.resolve())
