from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from app.services.intelligent_playbook_import import _normalize_ai_result


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _mock_tools(monkeypatch) -> None:
    tools = lambda: [{"name": "system.basics", "description": "Básico", "correction": False}]
    monkeypatch.setattr("app.services.intelligent_playbook_import.describe_tools", tools)
    monkeypatch.setattr("app.services.playbook_editor.describe_tools", tools)


def test_validation_text_is_not_split_into_characters(monkeypatch) -> None:
    _mock_tools(monkeypatch)
    draft = _normalize_ai_result(
        {
            "id": "snmp-validacao",
            "title": "Validação SNMP",
            "profiles": ["linux_generic"],
            "patterns": ["SNMP timeout"],
            "steps": [
                {
                    "tool": "system.basics",
                    "arguments": {},
                    "purpose": "Identificar o ambiente",
                }
            ],
            "validation_notes": "Após qualquer ajuste, repetir os testes de conectividade UDP 161 e SNMP.",
        },
        "procedimento.pdf",
    )

    assert draft["validation_notes"] == [
        "Após qualquer ajuste, repetir os testes de conectividade UDP 161 e SNMP."
    ]
    assert len(draft["validation_notes"]) == 1


def test_installer_enables_opencode_and_supports_nvm() -> None:
    main_installer = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
    full_installer = (PROJECT_ROOT / "scripts" / "install_all.sh").read_text(encoding="utf-8")
    setup = (PROJECT_ROOT / "scripts" / "setup_opencode.sh").read_text(encoding="utf-8")
    deploy_bootstrap = (PROJECT_ROOT / "deploy" / "scripts" / "bootstrap_opencode.sh").read_text(encoding="utf-8")
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci-release.yml").read_text(encoding="utf-8")

    assert 'OPENCODE_MODE="yes"' in main_installer
    assert "--with-opencode" in main_installer
    assert "OpenCode solicitado, mas npm não está instalado" not in full_installer
    assert 'bash "$APP_DIR/scripts/setup_opencode.sh"' in full_installer
    assert ".nvm/versions/node" in setup
    assert "NODE_MAJOR" in setup
    assert "allow-scripts=opencode-ai" in setup
    assert "--allow-scripts=opencode-ai" in setup
    assert "npm prefix -g" in setup
    assert '"OPENCODE_ENABLED": "true"' in setup
    assert "Falha ao configurar o OpenCode na linha" in setup
    assert "allow-scripts=opencode-ai" in deploy_bootstrap
    assert "--allow-scripts=opencode-ai" in deploy_bootstrap
    assert "npm prefix -g" in deploy_bootstrap
    assert '"OPENCODE_ENABLED": "true"' in deploy_bootstrap
    assert "systemctl --user restart agent-ia-api.service" in deploy_bootstrap
    assert "Instalar e configurar OpenCode integrado" in workflow
    assert "bootstrap_opencode.sh" in workflow


def test_install_scripts_have_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash não está disponível no ambiente de testes")

    for path in (
        PROJECT_ROOT / "install.sh",
        PROJECT_ROOT / "scripts" / "install_all.sh",
        PROJECT_ROOT / "scripts" / "setup_opencode.sh",
        PROJECT_ROOT / "deploy" / "scripts" / "bootstrap_opencode.sh",
    ):
        result = subprocess.run(
            [bash, "-n", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"
