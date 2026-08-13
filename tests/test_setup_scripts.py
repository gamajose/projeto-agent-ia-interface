from __future__ import annotations

from pathlib import Path
import os
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_installation_scripts_have_valid_bash_syntax() -> None:
    scripts = [
        PROJECT_ROOT / "install.sh",
        PROJECT_ROOT / "scripts" / "install_all.sh",
        PROJECT_ROOT / "scripts" / "setup_ollama.sh",
        PROJECT_ROOT / "scripts" / "stack_control.sh",
        PROJECT_ROOT / "scripts" / "setup_wsl.sh",
        PROJECT_ROOT / "scripts" / "start_web.sh",
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


def test_systemd_entry_scripts_are_executable_in_git_checkout() -> None:
    for relative in ("scripts/start_web.sh", "scripts/stack_control.sh"):
        path = PROJECT_ROOT / relative
        assert path.is_file()
        assert os.access(path, os.X_OK), f"{relative} precisa manter +x no Git para o systemd"


def test_setup_script_uses_linux_filesystem_and_python311_for_virtualenv() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_wsl.sh").read_text(encoding="utf-8")

    assert "$HOME/.venvs/$PROJECT_NAME" in content
    assert 'REQUIRED_PYTHON="3.11"' in content
    assert "sys.version_info[:2] == (3, 11)" in content
    assert "python3.11 python3" in content
    assert "python3.12 python3.11 python3" not in content
    assert "python3.11 python3.11-pip" in content
    assert "python3.11 python3.11-venv python3.11-dev" in content
    assert "UV_UNMANAGED_INSTALL" in content
    assert "UV_PYTHON_INSTALL_DIR" in content
    assert 'python install "$REQUIRED_PYTHON"' in content
    assert '"$PYTHON_BIN" -m venv "$VENV_DIR"' in content
    assert 'export PATH="$VENV_DIR/bin:$PATH"' in content
    assert "AGENT_PYTHON_BIN" in content
    assert "--break-system-packages" not in content
    assert '"$PIP" install -e "$PROJECT_DIR"' in content


def test_start_script_loads_dotenv_safely_before_server() -> None:
    content = (PROJECT_ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")

    assert 'ENV_FILE="${AGENT_ENV_FILE:-$PROJECT_DIR/.env}"' in content
    assert "from dotenv import dotenv_values" in content
    assert 'source "$ENV_FILE"' not in content
    assert 'export "$assignment"' in content
    assert "INSTALL_VENV_DIR" in content
    assert "CONFIGURED_VENV_DIR" in content
    assert "read_env_value AGENT_VENV_DIR" in content
    assert content.index("dotenv_values(path)") < content.index('exec "$AGENT_WEB"')


def test_bootstrap_uses_python311_target_user_and_safe_git_checkout() -> None:
    content = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

    assert "AGENT_INSTALL_ROOT:-/opt/agent-ia" in content
    assert "projeto-agent-ia-interface.git" in content
    assert "git clone" in content
    assert "scripts/install_all.sh" in content
    assert "não pode conter espaços" in content
    assert "runuser -u" in content
    assert "sys.version_info[:2] == (3, 11)" in content
    assert "python3.12" not in content
    assert "python3.11 /usr/bin/python3.11 /usr/local/bin/python3.11" in content
    assert "python install 3.11" in content
    assert "detect_target_user" in content
    assert "AGENT_INSTALL_USER" in content
    assert "Usuário operacional selecionado" in content
    assert "Normalizando ownership da aplicação" in content
    assert 'safe.directory=$APP_DIR' in content
    assert 'checkout -B "$REPO_REF" "origin/$REPO_REF"' in content
    assert "restore_mode_only_changes" in content
    assert "alterações locais reais" in content
    assert "prepare_ui_port" in content
    assert "wait_ui" in content
    assert "setup_ollama.sh" in content
    assert "AGENT_INSTALL_OLLAMA" in content
    assert 'systemctl restart agent-ia-web.service' in content
    assert "docker volume rm" not in content
    assert "reboot" not in content.lower()


def test_configurator_selects_dedicated_ports_and_updates_dsns() -> None:
    content = (PROJECT_ROOT / "scripts" / "configure_install_env.py").read_text(encoding="utf-8")

    assert "choose_service_port" in content
    assert "container_published_port" in content
    assert "port_available" in content
    assert 'key="POSTGRES_PORT"' in content
    assert 'key="REDIS_PORT"' in content
    assert '"POSTGRES_PORT": str(postgres_port)' in content
    assert '"REDIS_PORT": str(redis_port)' in content
    assert "127.0.0.1:{postgres_port}" in content
    assert "127.0.0.1:{redis_port}" in content
    assert "INSTALL_EXISTING_POSTGRES_PASSWORD" in content
    assert "INSTALL_EXISTING_REDIS_PASSWORD" in content
    assert "getpass" not in content
    assert "resolve_existing_password" in content
    assert "return \"\", False" in content


def test_stack_control_reconciles_ports_and_waits_for_redis_without_deleting_volumes() -> None:
    content = (PROJECT_ROOT / "scripts" / "stack_control.sh").read_text(encoding="utf-8")

    assert "POSTGRES_PORT" in content
    assert "REDIS_PORT" in content
    assert "container_published_port" in content
    assert "validate_service_port" in content
    assert "wait_redis_ready" in content
    assert 'up -d --no-deps "$service"' in content
    assert 'up -d --no-deps --force-recreate "$service"' in content
    assert "preservando o volume" in content
    assert "sync_postgres_password" in content
    assert "reconciliado pelo socket local em todo start" in content
    assert "Credencial do PostgreSQL local sincronizada com o .env sem apagar o volume" in content
    assert "Credencial do Redis local validada com o .env" in content
    assert "Reutilizando OmniRoute externo já ativo" in content
    assert "OmniRoute externo preservado" in content
    assert "docker volume rm" not in content
    assert " compose down" not in content


def test_compose_uses_configurable_local_ports_and_persistent_omniroute() -> None:
    content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "container_name: agent-ia-postgres" in content
    assert "container_name: agent-ia-redis" in content
    assert "container_name: omniroute" in content
    assert '${POSTGRES_PORT:-5432}:5432' in content
    assert '${REDIS_PORT:-6379}:6379' in content
    assert "diegosouzapw/omniroute:latest" in content
    assert "omniroute_data:/app/data" in content
    assert "127.0.0.1" in content
    assert "stop_grace_period: 40s" in content


def test_ollama_setup_is_local_idempotent_and_selects_small_model_for_low_ram() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_ollama.sh").read_text(encoding="utf-8")

    assert "https://ollama.com/install.sh" in content
    assert "llama3.2:1b" in content
    assert "llama3.2:3b" in content
    assert "MemTotal" in content
    assert "OLLAMA_MODELS=" in content
    assert "/opt/agent-ia" in content
    assert "ollama pull" in content
    assert "ollama show" in content
    assert "systemctl restart ollama.service" in content
    assert "OLLAMA_MODEL" in content
    assert "OLLAMA_BASE_URL" in content
    assert "reboot" not in content.lower()
    assert "shutdown" not in content.lower()
    assert "docker rm" not in content


def test_full_installer_creates_required_services_without_reboot() -> None:
    content = (PROJECT_ROOT / "scripts" / "install_all.sh").read_text(encoding="utf-8")

    assert "https://get.docker.com" in content
    assert "agent-ia-infra.service" in content
    assert "omniroute.service" in content
    assert "agent-ia-worker.service" in content
    assert "agent-ia-web.service" in content
    assert "ExecStart=$VENV_DIR/bin/agent-worker run" in content
    assert "Restart=always" in content
    assert 'systemctl is-active --quiet agent-ia-worker.service' in content
    assert 'python" -m app.db.init_db' in content
    assert "systemctl enable --now" in content
    assert "wait_omniroute 180" in content
    assert "reboot" not in content.lower()
    assert "shutdown" not in content.lower()
    assert "docker compose down" not in content
