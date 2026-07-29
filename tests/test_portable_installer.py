from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIGURATOR = PROJECT_ROOT / "scripts" / "configure_install_env.py"
EXAMPLE = PROJECT_ROOT / ".env.example"


def run_configurator(tmp_path: Path, *, extra_env: dict[str, str] | None = None) -> tuple[Path, Path, str]:
    install_root = tmp_path / "agent-ia"
    app_dir = install_root / "app"
    venv_dir = install_root / "venv"
    env_file = app_dir / ".env"
    omni_env = install_root / "config" / "omniroute.env"
    app_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(extra_env or {})

    result = subprocess.run(
        [
            sys.executable,
            str(CONFIGURATOR),
            "--env",
            str(env_file),
            "--example",
            str(EXAMPLE),
            "--omniroute-env",
            str(omni_env),
            "--install-root",
            str(install_root),
            "--app-dir",
            str(app_dir),
            "--venv-dir",
            str(venv_dir),
            "--operator",
            "José Operador",
            "--ssh-user",
            "2com",
            "--allowed-networks",
            "127.0.0.1/32,::1/128,192.168.28.0/24",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return env_file, omni_env, result.stdout


def test_configurator_generates_portable_paths_and_local_secrets(tmp_path: Path) -> None:
    env_file, omni_env, output = run_configurator(
        tmp_path,
        extra_env={
            "INSTALL_SSH_PASSWORD": "senha ssh com espaço",
            "INSTALL_OMNIROUTE_PASSWORD": "senha-painel-forte",
        },
    )

    values = dotenv_values(env_file)
    omni = dotenv_values(omni_env)
    install_root = tmp_path / "agent-ia"

    assert values["AGENT_INSTALL_ROOT"] == str(install_root)
    assert values["AGENT_VENV_DIR"] == str(install_root / "venv")
    assert values["AGENT_PLAYBOOK_DIR"] == str(install_root / "app" / "config" / "playbooks")
    assert values["AI_PROVIDER_REGISTRY_PATH"] == str(install_root / "data" / "providers.json")
    assert values["SSH_DEFAULT_PASSWORD"] == "senha ssh com espaço"
    assert values["AGENT_UI_ALLOWED_NETWORKS"] == "127.0.0.1/32,::1/128,192.168.28.0/24"
    assert values["POSTGRES_PASSWORD"] not in {"", "CHANGE_ME", None}
    assert values["REDIS_PASSWORD"] not in {"", "CHANGE_ME", None}
    assert values["POSTGRES_PASSWORD"] in values["POSTGRES_DSN"]
    assert values["REDIS_PASSWORD"] in values["REDIS_URL"]
    assert omni["INITIAL_PASSWORD"] == "senha-painel-forte"
    assert omni["STORAGE_DRIVER"] == "sqlite"
    assert omni["DATA_DIR"] == "/app/data"
    assert "senha ssh com espaço" not in output
    assert "senha-painel-forte" not in output
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(omni_env.stat().st_mode) == 0o600


def test_configurator_is_idempotent_and_preserves_existing_secrets(tmp_path: Path) -> None:
    env_file, omni_env, _ = run_configurator(tmp_path)
    first = dotenv_values(env_file)
    first_omni = dotenv_values(omni_env)

    env_file.write_text("# comentário preservado\n" + env_file.read_text(encoding="utf-8"), encoding="utf-8")
    env_file, omni_env, _ = run_configurator(tmp_path)
    second = dotenv_values(env_file)
    second_omni = dotenv_values(omni_env)

    assert second["POSTGRES_PASSWORD"] == first["POSTGRES_PASSWORD"]
    assert second["REDIS_PASSWORD"] == first["REDIS_PASSWORD"]
    assert second["APPROVAL_SECRET"] == first["APPROVAL_SECRET"]
    assert second["AGENT_API_TOKEN"] == first["AGENT_API_TOKEN"]
    assert second_omni["INITIAL_PASSWORD"] == first_omni["INITIAL_PASSWORD"]
    assert second_omni["JWT_SECRET"] == first_omni["JWT_SECRET"]
    assert env_file.read_text(encoding="utf-8").startswith("# comentário preservado")


def test_configurator_writes_optional_bastion_without_printing_password(tmp_path: Path) -> None:
    env_file, _omni_env, output = run_configurator(
        tmp_path,
        extra_env={
            "INSTALL_BASTION_HOST": "10.0.0.10",
            "INSTALL_BASTION_PORT": "2222",
            "INSTALL_BASTION_USER": "jose",
            "INSTALL_BASTION_PASSWORD": "segredo-bastion",
        },
    )
    values = dotenv_values(env_file)

    assert values["SSH_SRV_VPN_IP"] == "10.0.0.10"
    assert values["SSH_SRV_VPN_PORT"] == "2222"
    assert values["SSH_SRV_VPN_USER"] == "jose"
    assert values["SSH_SRV_VPN_SENHA"] == "segredo-bastion"
    assert "segredo-bastion" not in output
