from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.settings import PROJECT_ROOT, Settings, get_settings
from app.services.ai_providers import omniroute_route_options
from app.services.secrets import get_secret


class OpenCodeError(RuntimeError):
    """Erro esperado ao configurar ou iniciar o OpenCode."""


@dataclass(frozen=True)
class OpenCodeStatus:
    enabled: bool
    available: bool
    configured: bool
    command: str | None
    version: str
    workdir: str
    config_path: str
    provider: str
    model: str
    base_url: str
    web_host: str
    web_port: int
    web_url: str
    web_reachable: bool
    tunnel_command: str


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _directory_candidates(directory: Path) -> tuple[Path, ...]:
    return (
        directory / "opencode",
        directory / "bin" / "opencode",
        directory / "node_modules" / ".bin" / "opencode",
        directory / ".opencode" / "bin" / "opencode",
    )


def resolve_opencode_command(configured_path: str | None = None) -> str | None:
    """Localiza o executável sem interpretar argumentos livres."""
    if configured_path:
        configured = Path(configured_path).expanduser()
        if configured.is_dir():
            for candidate in _directory_candidates(configured):
                if _is_executable(candidate):
                    return str(candidate.resolve())
        elif _is_executable(configured):
            return str(configured.resolve())
        elif os.sep not in configured_path:
            found = shutil.which(configured_path)
            if found:
                return found

    return shutil.which("opencode")


def _workdir(settings: Settings) -> Path:
    return Path(settings.opencode_workdir).expanduser() if settings.opencode_workdir else PROJECT_ROOT


def _config_path(settings: Settings) -> Path:
    return Path(settings.opencode_config_path).expanduser()


def _route_rows(settings: Settings) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for route in omniroute_route_options(settings):
        model = str(route.model or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        rows.append((model, str(route.label or model)))

    fallback = (
        settings.opencode_model
        or settings.omniroute_default_route
        or settings.omniroute_model
        or "auto/coding"
    ).strip()
    if fallback and fallback not in seen:
        rows.insert(0, (fallback, fallback))
    return rows


def selected_opencode_model(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    routes = _route_rows(settings)
    preferred = (settings.opencode_model or "").strip()
    if preferred:
        return preferred
    for model, _ in routes:
        if model == (settings.omniroute_default_route or "").strip():
            return model
    return routes[0][0] if routes else "auto/coding"


def selected_opencode_small_model(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    preferred = (settings.opencode_small_model or "").strip()
    if preferred:
        return preferred
    routes = _route_rows(settings)
    for model, _ in routes:
        lowered = model.casefold()
        if any(token in lowered for token in ("fast", "cheap", "lite", "small")):
            return model
    return selected_opencode_model(settings)


def opencode_config(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    routes = _route_rows(settings)
    models = {
        model: {
            "name": f"OmniRoute · {label}",
        }
        for model, label in routes
    }
    default_model = selected_opencode_model(settings)
    small_model = selected_opencode_small_model(settings)
    models.setdefault(default_model, {"name": f"OmniRoute · {default_model}"})
    models.setdefault(small_model, {"name": f"OmniRoute · {small_model}"})

    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"omniroute/{default_model}",
        "small_model": f"omniroute/{small_model}",
        "default_agent": settings.opencode_default_agent,
        "share": "disabled",
        "enabled_providers": ["omniroute"],
        "provider": {
            "omniroute": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "OmniRoute",
                "options": {
                    "baseURL": "{env:OPENCODE_OMNIROUTE_BASE_URL}",
                    "apiKey": "{env:OMNIROUTE_API_KEY}",
                    "timeout": 600000,
                },
                "models": models,
            }
        },
        "permission": {
            "edit": "ask",
            "bash": "ask",
            "external_directory": "deny",
            "webfetch": "ask",
            "websearch": "ask",
        },
        "server": {
            "hostname": settings.opencode_web_host,
            "port": settings.opencode_web_port,
        },
    }


def ensure_opencode_config(
    settings: Settings | None = None,
    *,
    force: bool = True,
) -> Path:
    settings = settings or get_settings()
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and not force:
        return path

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(opencode_config(settings), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return path


def _omniroute_token(settings: Settings) -> str:
    try:
        token = get_secret("OMNIROUTE_API_KEY", settings.omniroute_api_key, settings=settings)
    except Exception as exc:
        raise OpenCodeError("não foi possível consultar o token do OmniRoute") from exc
    if not token:
        raise OpenCodeError("OMNIROUTE_API_KEY não está configurada para o OpenCode")
    return token


def opencode_environment(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    environment = os.environ.copy()
    environment["OMNIROUTE_API_KEY"] = _omniroute_token(settings)
    environment["OPENCODE_OMNIROUTE_BASE_URL"] = settings.omniroute_base_url.rstrip("/")
    environment["OPENCODE_CONFIG"] = str(_config_path(settings))
    environment["OPENCODE_SERVER_USERNAME"] = settings.opencode_server_username
    if settings.opencode_server_password:
        environment["OPENCODE_SERVER_PASSWORD"] = settings.opencode_server_password
    environment.setdefault("BROWSER", "/bin/true")
    return environment


def _web_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except OSError:
        return False


def _tunnel_command(settings: Settings) -> str:
    host = settings.opencode_tunnel_host or "IP_DA_VM"
    user = settings.opencode_tunnel_user or os.getenv("USER") or "USUARIO"
    port = settings.opencode_tunnel_ssh_port
    return (
        f"ssh -N -L {settings.opencode_web_port}:127.0.0.1:{settings.opencode_web_port} "
        f"{user}@{host} -p {port}"
    )


def opencode_status(settings: Settings | None = None) -> OpenCodeStatus:
    settings = settings or get_settings()
    command = resolve_opencode_command(settings.opencode_cli_path)
    path = _config_path(settings)
    version = "não identificado"
    if command:
        try:
            completed = subprocess.run(
                [command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = (completed.stdout or completed.stderr or "").strip()
            if output:
                version = output.splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            version = "instalado, versão indisponível"

    token_configured = False
    try:
        token_configured = bool(get_secret("OMNIROUTE_API_KEY", settings.omniroute_api_key, settings=settings))
    except Exception:
        token_configured = False

    return OpenCodeStatus(
        enabled=settings.opencode_enabled,
        available=bool(command),
        configured=bool(command and path.is_file() and token_configured),
        command=command,
        version=version,
        workdir=str(_workdir(settings)),
        config_path=str(path),
        provider="OmniRoute",
        model=selected_opencode_model(settings),
        base_url=settings.omniroute_base_url,
        web_host=settings.opencode_web_host,
        web_port=settings.opencode_web_port,
        web_url=settings.opencode_web_url,
        web_reachable=_web_reachable(settings.opencode_web_host, settings.opencode_web_port),
        tunnel_command=_tunnel_command(settings),
    )


def launch_opencode(settings: Settings | None = None) -> int:
    """Abre o OpenCode TUI usando OmniRoute e o diretório configurado."""
    settings = settings or get_settings()
    status = opencode_status(settings)
    if not settings.opencode_enabled:
        raise OpenCodeError("OpenCode está desabilitado na configuração")
    if not status.command:
        raise OpenCodeError(
            "OpenCode não encontrado. Execute scripts/setup_opencode.sh ou configure OPENCODE_CLI_PATH."
        )
    workdir = Path(status.workdir)
    if not workdir.is_dir():
        raise OpenCodeError(f"diretório do OpenCode não existe: {workdir}")

    ensure_opencode_config(settings)
    try:
        completed = subprocess.run(
            [status.command],
            cwd=str(workdir),
            env=opencode_environment(settings),
            check=False,
        )
    except OSError as exc:
        raise OpenCodeError(f"não foi possível iniciar o OpenCode: {exc}") from exc
    return int(completed.returncode)


def launch_opencode_web(settings: Settings | None = None) -> int:
    """Executa a interface web somente no endereço configurado."""
    settings = settings or get_settings()
    status = opencode_status(settings)
    if not settings.opencode_enabled:
        raise OpenCodeError("OpenCode está desabilitado na configuração")
    if not status.command:
        raise OpenCodeError("OpenCode não encontrado; execute scripts/setup_opencode.sh")
    if not settings.opencode_server_password:
        raise OpenCodeError("OPENCODE_SERVER_PASSWORD é obrigatória para a interface web")

    workdir = Path(status.workdir)
    if not workdir.is_dir():
        raise OpenCodeError(f"diretório do OpenCode não existe: {workdir}")
    ensure_opencode_config(settings)
    command = [
        status.command,
        "web",
        "--hostname",
        settings.opencode_web_host,
        "--port",
        str(settings.opencode_web_port),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(workdir),
            env=opencode_environment(settings),
            check=False,
        )
    except OSError as exc:
        raise OpenCodeError(f"não foi possível iniciar o OpenCode Web: {exc}") from exc
    return int(completed.returncode)


def web_main() -> None:
    raise SystemExit(launch_opencode_web())


def main() -> None:
    parser = argparse.ArgumentParser(description="Integração OpenCode + OmniRoute")
    parser.add_argument("--configure", action="store_true", help="gera o opencode.json seguro")
    parser.add_argument("--status", action="store_true", help="mostra status sem revelar segredos")
    parser.add_argument("--web", action="store_true", help="inicia a interface web")
    args = parser.parse_args()
    settings = get_settings()
    if args.configure:
        print(ensure_opencode_config(settings))
        return
    if args.status:
        print(json.dumps(asdict(opencode_status(settings)), ensure_ascii=False, indent=2))
        return
    if args.web:
        raise SystemExit(launch_opencode_web(settings))
    raise SystemExit(launch_opencode(settings))


if __name__ == "__main__":
    main()
