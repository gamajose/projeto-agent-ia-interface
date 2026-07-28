from __future__ import annotations

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_wsl_scripts_have_valid_bash_syntax() -> None:
    scripts = [
        PROJECT_ROOT / "scripts" / "setup_wsl.sh",
        PROJECT_ROOT / "scripts" / "start_web.sh",
    ]

    result = subprocess.run(
        ["bash", "-n", *(str(path) for path in scripts)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_setup_script_uses_linux_filesystem_for_virtualenv() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_wsl.sh").read_text(encoding="utf-8")

    assert "$HOME/.venvs/$PROJECT_NAME" in content
    assert "--break-system-packages" not in content
    assert '"$PIP" install -e "$PROJECT_DIR"' in content


def test_start_script_loads_dotenv_safely_before_server() -> None:
    content = (PROJECT_ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")

    assert 'ENV_FILE="${AGENT_ENV_FILE:-$PROJECT_DIR/.env}"' in content
    assert "from dotenv import dotenv_values" in content
    assert 'source "$ENV_FILE"' not in content
    assert 'export "$assignment"' in content
    assert content.index("dotenv_values(path)") < content.index('exec "$AGENT_WEB"')
