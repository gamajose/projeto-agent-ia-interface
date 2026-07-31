from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.core.settings import PROJECT_ROOT, Settings


def _candidate_paths(settings: Settings | None = None) -> tuple[Path, ...]:
    paths: list[Path] = []
    explicit = str(os.getenv("AGENT_ENV_FILE") or "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    if settings is not None:
        configured = str(getattr(settings, "ai_settings_env_path", "") or "").strip()
        if configured:
            paths.append(Path(configured).expanduser())
    paths.append(PROJECT_ROOT / ".env")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def runtime_value(
    name: str,
    default: Any = None,
    *,
    settings: Settings | None = None,
) -> Any:
    """Lê uma opção operacional sem depender do ambiente exportado pelo systemd."""
    direct = os.getenv(name)
    if direct is not None:
        return direct
    for path in _candidate_paths(settings):
        if not path.is_file():
            continue
        value = dotenv_values(path).get(name)
        if value is not None:
            return value
    return default


def runtime_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = 65535,
    settings: Settings | None = None,
) -> int:
    raw = str(runtime_value(name, "", settings=settings) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} precisa ser um número inteiro") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} precisa estar entre {minimum} e {maximum}")
    return value


def runtime_bool(
    name: str,
    default: bool,
    *,
    settings: Settings | None = None,
) -> bool:
    raw = runtime_value(name, None, settings=settings)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    normalized = str(raw).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "sim", "s"}:
        return True
    if normalized in {"0", "false", "no", "off", "nao", "não", "n"}:
        return False
    raise ValueError(f"{name} precisa ser true/false")
