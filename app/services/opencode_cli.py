from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
    models: tuple[dict[str, str], ...]
    base_url: str
    web_host: str
    web_port: int
    web_url: str
    web_reachable: bool
    tunnel_command: str
    interface_enabled: bool
    allow_build: bool
    active_runs: int


_RUNS: dict[str, dict[str, Any]] = {}
_RUN_LOCK = threading.RLock()
_TERMINAL_STATES = {"completed", "failed", "timeout"}
_SECRET_KEYS = {"authorization", "api_key", "apikey", "token", "password", "secret"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


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


def _active_run_count() -> int:
    with _RUN_LOCK:
        return sum(1 for item in _RUNS.values() if item.get("status") not in _TERMINAL_STATES)


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

    models = tuple(
        {"value": model, "label": label}
        for model, label in _route_rows(settings)
    )
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
        models=models,
        base_url=settings.omniroute_base_url,
        web_host=settings.opencode_web_host,
        web_port=settings.opencode_web_port,
        web_url=settings.opencode_web_url,
        web_reachable=_web_reachable(settings.opencode_web_host, settings.opencode_web_port),
        tunnel_command=_tunnel_command(settings),
        interface_enabled=settings.opencode_interface_enabled,
        allow_build=settings.opencode_interface_allow_build,
        active_runs=_active_run_count(),
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(secret in lowered for secret in _SECRET_KEYS):
                continue
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    return value


def _extract_session_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold().replace("_", "") in {"sessionid", "session"}:
                if isinstance(item, str) and item.strip():
                    return item.strip()
                if isinstance(item, dict):
                    nested = item.get("id")
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
            found = _extract_session_id(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_session_id(item)
            if found:
                return found
    return None


def _extract_event_text(value: Any) -> list[str]:
    rows: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).casefold()
            if lowered in {"text", "content", "message", "output", "reason"} and isinstance(item, str):
                text = item.strip()
                if text:
                    rows.append(text)
            elif isinstance(item, (dict, list)):
                rows.extend(_extract_event_text(item))
    elif isinstance(value, list):
        for item in value:
            rows.extend(_extract_event_text(item))
    return rows


def _parse_run_output(raw: str, max_output_chars: int) -> tuple[list[dict[str, Any]], str, str | None]:
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    session_id: str | None = None
    plain_lines: list[str] = []

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            plain_lines.append(line)
            continue
        if not isinstance(payload, dict):
            plain_lines.append(line)
            continue
        sanitized = _sanitize(payload)
        events.append(sanitized)
        session_id = session_id or _extract_session_id(sanitized)
        text_parts.extend(_extract_event_text(sanitized))

    combined_rows: list[str] = []
    seen: set[str] = set()
    for item in [*text_parts, *plain_lines]:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        combined_rows.append(normalized)
    output = "\n\n".join(combined_rows).strip()
    if not output and raw.strip():
        output = raw.strip()
    if len(output) > max_output_chars:
        output = output[:max_output_chars] + "\n\n[saída reduzida pelo limite da interface]"
    return events[-300:], output, session_id


def _build_run_command(
    *,
    status: OpenCodeStatus,
    prompt: str,
    agent: str,
    model: str,
    session_id: str | None,
    auto_approve: bool,
) -> list[str]:
    if not status.command:
        raise OpenCodeError("OpenCode não está instalado")
    title = " ".join(prompt.split())[:80] or "OpenCode pela interface"
    command = [
        status.command,
        "run",
        "--format",
        "json",
        "--agent",
        agent,
        "--model",
        f"omniroute/{model}",
        "--title",
        title,
    ]
    if status.web_reachable:
        command.extend(["--attach", status.web_url, "--dir", status.workdir])
    if session_id:
        command.extend(["--session", session_id])
    if auto_approve:
        command.append("--auto")
    command.append(prompt)
    return command


def _public_run(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "status": record["status"],
        "agent": record["agent"],
        "model": record["model"],
        "prompt": record["prompt"],
        "session_id": record.get("session_id"),
        "output": record.get("output", ""),
        "events": record.get("events", []),
        "error": record.get("error"),
        "returncode": record.get("returncode"),
        "created_at": record["created_at"],
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "duration_ms": record.get("duration_ms"),
        "auto_approve": bool(record.get("auto_approve")),
    }


def _run_worker(
    job_id: str,
    prompt: str,
    agent: str,
    model: str,
    session_id: str | None,
    auto_approve: bool,
    settings: Settings,
) -> None:
    started_monotonic = time.monotonic()
    with _RUN_LOCK:
        record = _RUNS[job_id]
        record["status"] = "running"
        record["started_at"] = _now()

    try:
        status = opencode_status(settings)
        command = _build_run_command(
            status=status,
            prompt=prompt,
            agent=agent,
            model=model,
            session_id=session_id,
            auto_approve=auto_approve,
        )
        ensure_opencode_config(settings)
        completed = subprocess.run(
            command,
            cwd=status.workdir,
            env=opencode_environment(settings),
            capture_output=True,
            text=True,
            timeout=settings.opencode_run_timeout_seconds,
            check=False,
        )
        raw = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        events, output, discovered_session = _parse_run_output(
            raw,
            settings.opencode_run_max_output_chars,
        )
        with _RUN_LOCK:
            record = _RUNS[job_id]
            record["returncode"] = int(completed.returncode)
            record["events"] = events
            record["output"] = output
            record["session_id"] = discovered_session or session_id
            record["status"] = "completed" if completed.returncode == 0 else "failed"
            if completed.returncode != 0:
                record["error"] = output or f"OpenCode encerrou com código {completed.returncode}"
    except subprocess.TimeoutExpired:
        with _RUN_LOCK:
            record = _RUNS[job_id]
            record["status"] = "timeout"
            record["error"] = (
                f"O OpenCode excedeu o limite de {settings.opencode_run_timeout_seconds} segundos."
            )
    except Exception as exc:
        with _RUN_LOCK:
            record = _RUNS[job_id]
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with _RUN_LOCK:
            record = _RUNS[job_id]
            record["finished_at"] = _now()
            record["duration_ms"] = max(0, int((time.monotonic() - started_monotonic) * 1000))


def submit_opencode_run(
    prompt: str,
    *,
    agent: str = "plan",
    model: str | None = None,
    session_id: str | None = None,
    auto_approve: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    cleaned_prompt = prompt.strip()
    normalized_agent = agent.strip().casefold()
    selected_model = (model or selected_opencode_model(settings)).strip()

    if not settings.opencode_enabled or not settings.opencode_interface_enabled:
        raise OpenCodeError("OpenCode integrado está desabilitado")
    if len(cleaned_prompt) < 3:
        raise OpenCodeError("descreva a tarefa para o OpenCode")
    if len(cleaned_prompt) > settings.opencode_run_max_prompt_chars:
        raise OpenCodeError(
            f"a solicitação excede {settings.opencode_run_max_prompt_chars} caracteres"
        )
    if normalized_agent not in {"plan", "build"}:
        raise OpenCodeError("agente inválido; use plan ou build")
    if normalized_agent == "build" and not settings.opencode_interface_allow_build:
        raise OpenCodeError("o modo aplicar está desabilitado na configuração")
    valid_models = {item[0] for item in _route_rows(settings)}
    if selected_model not in valid_models:
        raise OpenCodeError("a rota selecionada não está configurada no OmniRoute")

    status = opencode_status(settings)
    if not status.available or not status.configured:
        raise OpenCodeError(
            "OpenCode não está pronto; execute scripts/setup_opencode.sh e valide o OmniRoute"
        )
    if _active_run_count() >= settings.opencode_run_concurrency:
        raise OpenCodeError(
            f"já existem {settings.opencode_run_concurrency} execução(ões) ativa(s); aguarde a conclusão"
        )

    job_id = str(uuid.uuid4())
    record = {
        "id": job_id,
        "status": "queued",
        "agent": normalized_agent,
        "model": selected_model,
        "prompt": cleaned_prompt,
        "session_id": session_id.strip() if session_id else None,
        "auto_approve": bool(auto_approve),
        "output": "",
        "events": [],
        "error": None,
        "returncode": None,
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
    }
    with _RUN_LOCK:
        _RUNS[job_id] = record

    thread = threading.Thread(
        target=_run_worker,
        args=(
            job_id,
            cleaned_prompt,
            normalized_agent,
            selected_model,
            record["session_id"],
            bool(auto_approve),
            settings,
        ),
        name=f"opencode-run-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return _public_run(record)


def get_opencode_run(job_id: str) -> dict[str, Any] | None:
    with _RUN_LOCK:
        record = _RUNS.get(job_id)
        return _public_run(record) if record else None


def list_opencode_runs(limit: int = 20) -> list[dict[str, Any]]:
    resolved_limit = max(1, min(int(limit), 100))
    with _RUN_LOCK:
        ordered = sorted(
            _RUNS.values(),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )
        return [_public_run(item) for item in ordered[:resolved_limit]]


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
