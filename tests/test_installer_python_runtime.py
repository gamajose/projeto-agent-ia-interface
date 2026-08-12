from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_wsl_requires_exact_python_311_and_recreates_wrong_venv() -> None:
    source = (ROOT / "scripts" / "setup_wsl.sh").read_text(encoding="utf-8")
    assert "sys.version_info[:2] == (3, 11)" in source
    assert "python3.14" not in source
    assert "recriando obrigatoriamente com Python 3.11.x" in source
    assert "python install 3.11" in source
    assert 'export PATH="$VENV_DIR/bin:$PATH"' in source
    assert "ensure_shell_path" in source


def test_project_metadata_rejects_python_312_and_newer() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11,<3.12"' in pyproject


def test_stack_preflight_keeps_volume_and_syncs_local_agent_password() -> None:
    source = (ROOT / "scripts" / "stack_control.sh").read_text(encoding="utf-8")
    assert "postgres_password_valid" in source
    assert "sync_postgres_password" in source
    assert "ALTER ROLE agent_ia WITH PASSWORD" in source
    assert "sem apagar o volume" in source
    assert "docker volume rm" not in source
