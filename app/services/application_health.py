from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from redis import Redis
from sqlalchemy import text

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal
from app.services.playbooks import list_playbooks
from app.services.provider_preflight import preflight_all


def _package_version() -> str:
    try:
        return version("agent-ia-infra")
    except PackageNotFoundError:
        return "desconhecida"


def _git_value(project_root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), *arguments],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _database_health() -> dict[str, Any]:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {"state": "available", "detail": "Conexão e consulta simples validadas."}
    except Exception as exc:
        return {
            "state": "unavailable",
            "detail": f"{type(exc).__name__}: não foi possível validar o PostgreSQL.",
        }


def _queue_health(settings: Settings) -> dict[str, Any]:
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        depth = int(client.llen(settings.agent_queue_name))
        return {
            "state": "available",
            "detail": "Redis respondeu ao diagnóstico.",
            "queue": settings.agent_queue_name,
            "depth": depth,
            "execution_mode": settings.agent_execution_mode,
        }
    except Exception as exc:
        state = "not_configured" if settings.agent_execution_mode.strip().casefold() != "queue" else "unavailable"
        return {
            "state": state,
            "detail": (
                "Redis não está disponível, mas o modo atual é inline."
                if state == "not_configured"
                else f"{type(exc).__name__}: fila indisponível."
            ),
            "queue": settings.agent_queue_name,
            "depth": None,
            "execution_mode": settings.agent_execution_mode,
        }


def application_health(settings: Settings | None = None) -> dict[str, Any]:
    """Retorna apenas metadados operacionais seguros, nunca credenciais."""
    settings = settings or get_settings()
    project_root = Path(__file__).resolve().parents[2]
    playbook_dir = Path(settings.agent_playbook_dir).expanduser()
    providers = [item.model_dump(mode="json") for item in preflight_all(settings)]
    database = _database_health()
    queue = _queue_health(settings)
    selectable_providers = sum(1 for item in providers if item.get("selectable"))

    overall = "healthy"
    if database["state"] != "available":
        overall = "critical"
    elif selectable_providers == 0 or queue["state"] == "unavailable":
        overall = "attention"

    return {
        "status": overall,
        "version": _package_version(),
        "git": {
            "branch": _git_value(project_root, "rev-parse", "--abbrev-ref", "HEAD") or "desconhecida",
            "commit": _git_value(project_root, "rev-parse", "--short", "HEAD") or "desconhecido",
        },
        "database": database,
        "queue": queue,
        "providers": providers,
        "playbooks": {
            "state": "available" if playbook_dir.is_dir() else "unavailable",
            "directory": str(playbook_dir),
            "count": len(list_playbooks()),
        },
        "worker": {
            "state": "external" if settings.agent_execution_mode.strip().casefold() == "queue" else "inline",
            "detail": (
                "As investigações são consumidas pelo agent-worker."
                if settings.agent_execution_mode.strip().casefold() == "queue"
                else "As investigações são executadas pelo processo web."
            ),
        },
        "recent_errors": [],
    }
