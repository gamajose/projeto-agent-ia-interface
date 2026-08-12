from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIGURATOR = PROJECT_ROOT / "scripts" / "configure_install_env.py"


def load_configurator_module():
    spec = importlib.util.spec_from_file_location("configure_install_env_runtime_test", CONFIGURATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_postgres_password_is_recovered_before_prompt(monkeypatch) -> None:
    module = load_configurator_module()
    monkeypatch.delenv("INSTALL_EXISTING_POSTGRES_PASSWORD", raising=False)
    monkeypatch.setattr(module, "container_exists", lambda container: True)
    monkeypatch.setattr(module, "container_running", lambda container: True)
    monkeypatch.setattr(
        module,
        "prompt_secret",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("não deveria pedir senha")),
    )

    value, prompted = module.resolve_existing_password(
        container="agent-ia-postgres",
        environment_name="INSTALL_EXISTING_POSTGRES_PASSWORD",
        current_password="senha-do-env-incorreta",
        prompt="Senha atual do PostgreSQL: ",
        validator=lambda password: password == "senha-real-do-container",
        recoverer=lambda: "senha-real-do-container",
    )

    assert value == "senha-real-do-container"
    assert prompted is False


def test_postgres_password_is_read_from_container_metadata(monkeypatch) -> None:
    module = load_configurator_module()
    calls: list[tuple[str, str]] = []

    def fake_value(container: str, key: str) -> str:
        calls.append((container, key))
        return "senha-existente"

    monkeypatch.setattr(module, "container_environment_value", fake_value)

    assert module.recover_postgres_password() == "senha-existente"
    assert calls == [("agent-ia-postgres", "POSTGRES_PASSWORD")]


def test_redis_password_is_recovered_from_requirepass_command(monkeypatch) -> None:
    module = load_configurator_module()
    monkeypatch.setattr(module, "container_environment_value", lambda container, key: "")
    monkeypatch.setattr(
        module,
        "container_command",
        lambda container: ["redis-server", "--appendonly", "yes", "--requirepass", "redis-real"],
    )

    assert module.recover_redis_password() == "redis-real"


def test_docker_command_uses_noninteractive_sudo_only_on_socket_permission_error(monkeypatch) -> None:
    module = load_configurator_module()
    calls: list[list[str]] = []

    def fake_run(command, *, timeout, environment, stdin_text):
        calls.append(list(command))
        if command[0] == "docker":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="permission denied while trying to connect to the Docker daemon socket")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/sudo" if name == "sudo" else None)

    result = module.docker_command("inspect", "agent-ia-postgres")

    assert result is not None
    assert result.returncode == 0
    assert calls[0] == ["docker", "inspect", "agent-ia-postgres"]
    assert calls[1] == ["sudo", "-n", "docker", "inspect", "agent-ia-postgres"]


def test_setup_runtime_rejects_python314_and_recreates_non311_venv() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_wsl.sh").read_text(encoding="utf-8")

    assert "sys.version_info[:2] == (3, 11)" in content
    assert "Python 3.12, 3.13, 3.14" in content
    assert "ambiente virtual existente não usa Python $REQUIRED_PYTHON" in content
    assert "rm -rf -- \"$VENV_DIR\"" in content
    assert "UV_PYTHON_INSTALL_DIR" in content
