from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_new_wsl_ai_scripts_have_valid_syntax() -> None:
    scripts = [
        PROJECT_ROOT / "scripts" / "setup_ai_stack.sh",
        PROJECT_ROOT / "scripts" / "update_wsl.sh",
    ]
    result = subprocess.run(
        ["bash", "-n", *(str(path) for path in scripts)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_env_schema_sync_preserves_secrets_and_corrects_paths(tmp_path: Path) -> None:
    app_dir = tmp_path / "agent-ia"
    app_dir.mkdir()
    env_file = app_dir / ".env"
    example = app_dir / ".env.example"
    venv_dir = tmp_path / "venv"
    omniroute_env = app_dir / "config" / "omniroute.env"

    env_file.write_text(
        "\n".join(
            [
                "GEMINI_API_KEY=segredo-gemini",
                "GROQ_API_KEY=segredo-groq",
                "GOOGLE_GEMINI_API_KEY=alias-antigo",
                "AI_SETTINGS_ENV_PATH=/opt/agent-ia/app/.env",
                "AGENT_UI_PORT=8080",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    example.write_text(
        "GEMINI_API_KEY=\nGROQ_API_KEY=\nDEEPSEEK_API_KEY=\nOLLAMA_BASE_URL=http://127.0.0.1:11434\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "sync_env_schema.py"),
            "--env",
            str(env_file),
            "--example",
            str(example),
            "--install-root",
            str(app_dir),
            "--app-dir",
            str(app_dir),
            "--venv-dir",
            str(venv_dir),
            "--omniroute-env",
            str(omniroute_env),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    content = env_file.read_text(encoding="utf-8")

    assert "GEMINI_API_KEY=segredo-gemini" in content
    assert "GROQ_API_KEY=segredo-groq" in content
    assert f"AI_SETTINGS_ENV_PATH={env_file.resolve()}" in content
    assert f"AGENT_VENV_DIR={venv_dir.resolve()}" in content
    assert f"OMNIROUTE_ENV_FILE={omniroute_env.resolve()}" in content
    assert "DEEPSEEK_API_KEY=" in content
    assert payload["recognized_api_keys"] == ["GEMINI_API_KEY", "GROQ_API_KEY"]
    assert "segredo-gemini" not in result.stdout
    assert payload["backup"]
    assert Path(payload["backup"]).is_file()


def test_env_schema_migrates_legacy_gemini_alias(tmp_path: Path) -> None:
    app_dir = tmp_path / "agent-ia"
    app_dir.mkdir()
    env_file = app_dir / ".env"
    example = app_dir / ".env.example"
    env_file.write_text("GOOGLE_API_KEY=chave-legada\n", encoding="utf-8")
    example.write_text("GEMINI_API_KEY=\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "sync_env_schema.py"),
            "--env",
            str(env_file),
            "--example",
            str(example),
            "--install-root",
            str(app_dir),
            "--app-dir",
            str(app_dir),
            "--venv-dir",
            str(tmp_path / "venv"),
            "--omniroute-env",
            str(app_dir / "config" / "omniroute.env"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "GEMINI_API_KEY=chave-legada" in env_file.read_text(encoding="utf-8")
    assert "GOOGLE_API_KEY->GEMINI_API_KEY" in result.stdout


def test_update_script_never_discards_local_changes() -> None:
    content = (PROJECT_ROOT / "scripts" / "update_wsl.sh").read_text(encoding="utf-8")
    assert "git status --porcelain" in content
    assert "git merge --ff-only" in content
    assert "git reset" not in content
    assert "git clean" not in content
    assert "stash pop" not in content


def test_ai_stack_installs_local_services_and_hides_api_values() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_ai_stack.sh").read_text(encoding="utf-8")
    assert "https://ollama.com/install.sh" in content
    assert "llama3.2:1b" in content
    assert "docker compose" in content
    assert "omniroute" in content
    assert "sync_env_schema.py" in content
    assert "os valores não serão exibidos" in content
    assert "reboot" not in content.lower()
    assert "shutdown" not in content.lower()
