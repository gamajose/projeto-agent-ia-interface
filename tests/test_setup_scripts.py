from __future__ import annotations

from pathlib import Path
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
    ]

    result = subprocess.run(
        ["bash", "-n", *(str(path) for path in scripts)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_setup_script_uses_linux_filesystem_and_supported_python_for_virtualenv() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_wsl.sh").read_text(encoding="utf-8")

    assert "$HOME/.venvs/$PROJECT_NAME" in content
    assert "sys.version_info >= (3, 11)" in content
    assert "python3.12 python3.11 python3" in content
    assert "python3.11 python3.11-pip" in content
    assert "ambiente virtual existente usa Python incompatível" in content
    assert '"$PYTHON_BIN" -m venv "$VENV_DIR"' in content
    assert "--break-system-packages" not in content
    assert '"$PIP" install -e "$PROJECT_DIR"' in content


def test_start_script_loads_dotenv_safely_before_server() -> None:
    content = (PROJECT_ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")

    assert 'ENV_FILE="${AGENT_ENV_FILE:-$PROJECT_DIR/.env}"' in content
    assert "from dotenv import dotenv_values" in content
    assert 'source "$ENV_FILE"' not in content
    assert 'export "$assignment"' in content
    assert content.index("dotenv_values(path)") < content.index('exec "$AGENT_WEB"')


def test_bootstrap_uses_predictable_path_and_supports_remote_install() -> None:
    content = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

    assert "AGENT_INSTALL_ROOT:-/opt/agent-ia" in content
    assert "projeto-agent-ia-interface.git" in content
    assert "git clone" in content
    assert "merge --ff-only" in content
    assert "scripts/install_all.sh" in content
    assert "não pode conter espaços" in content
    assert "runuser -u" in content
    assert "sys.version_info >= (3, 11)" in content
    assert "dnf install -y python3.11 python3.11-pip" in content
    assert "restore_mode_only_changes" in content
    assert "hash-object" in content
    assert "ls-files -s" in content
    assert "core.fileMode=false" not in content
    assert "alterações locais reais" in content
    assert "prepare_ui_port" in content
    assert "wait_ui" in content
    assert "setup_ollama.sh" in content
    assert "AGENT_INSTALL_OLLAMA" in content
    assert 'systemctl restart agent-ia-web.service' in content
    assert 'systemctl is-active --quiet agent-ia-web.service' in content
    assert "rm -rf" not in content


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
    assert "wait_container omniroute" not in content
    assert "reboot" not in content.lower()
    assert "shutdown" not in content.lower()
    assert "docker compose down" not in content


def test_configurator_prompts_and_validates_existing_service_passwords() -> None:
    content = (PROJECT_ROOT / "scripts" / "configure_install_env.py").read_text(encoding="utf-8")

    assert "resolve_existing_password" in content
    assert "getpass.getpass" in content
    assert "Senha atual do PostgreSQL" in content
    assert "Senha atual do Redis" in content
    assert "validate_postgres_password" in content
    assert "validate_redis_password" in content
    assert "INSTALL_EXISTING_POSTGRES_PASSWORD" in content
    assert "INSTALL_EXISTING_REDIS_PASSWORD" in content
    assert '"PGPASSWORD"' in content
    assert '"REDISCLI_AUTH"' in content
    assert 'f"PGPASSWORD={password}"' not in content
    assert 'f"REDISCLI_AUTH={password}"' not in content
    assert "POSTGRES_PASSWORD=" not in content.split("def container_exists", 1)[0]


def test_stack_control_reuses_containers_and_external_omniroute() -> None:
    content = (PROJECT_ROOT / "scripts" / "stack_control.sh").read_text(encoding="utf-8")

    assert "container_exists" in content
    assert "Reutilizando container ativo" in content
    assert "Reutilizando OmniRoute externo já ativo" in content
    assert "OmniRoute externo preservado" in content
    assert "OMNIROUTE_MODE_FILE" in content
    assert "omniroute_http_ready" in content
    assert "docker compose" not in content  # comando é montado por array para usar o plugin v2
    assert '"${COMPOSE[@]}" up -d "$service"' in content
    assert "docker rm" not in content
    assert " compose down" not in content


def test_compose_includes_persistent_omniroute() -> None:
    content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "container_name: agent-ia-postgres" in content
    assert "container_name: agent-ia-redis" in content
    assert "container_name: omniroute" in content
    assert "diegosouzapw/omniroute:latest" in content
    assert "omniroute_data:/app/data" in content
    assert "127.0.0.1" in content
    assert "stop_grace_period: 40s" in content
