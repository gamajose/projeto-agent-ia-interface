from __future__ import annotations

import json
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from redis import Redis

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.redaction import redact_object
from app.services.runner import run_target


class JobError(RuntimeError):
    pass


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _result_key(settings: Settings, job_id: str) -> str:
    return f"{settings.agent_result_prefix}{job_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store(client: Redis, settings: Settings, job_id: str, payload: dict[str, Any]) -> None:
    client.setex(
        _result_key(settings, job_id),
        max(60, int(settings.agent_job_ttl_seconds)),
        json.dumps(redact_object(payload), ensure_ascii=False, default=str),
    )


def _default_provider(settings: Settings) -> str:
    return str(getattr(settings, "ai_provider", "gemini") or "gemini").strip().lower()


def enqueue_investigation(
    reference: str,
    objective: str,
    *,
    environment: EnvironmentType = EnvironmentType.UNKNOWN,
    mode: str = "propose",
    approve: bool = False,
    ssh_port: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    playbook_mode: str = "auto",
    playbook_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if mode == "correct" and approve:
        # Uma fila distribuída nunca transforma intenção em autorização implícita.
        approve = False
        mode = "propose"
    job_id = str(uuid.uuid4())
    selection = {
        "provider": (provider_name or _default_provider(settings)).strip().lower(),
        "model": (model_name or "").strip(),
        "playbook_mode": (playbook_mode or "auto").strip().lower(),
        "playbook_id": (playbook_id or "").strip() or None,
    }
    job = {
        "job_id": job_id,
        "reference": reference,
        "objective": objective,
        "environment": environment.value,
        "mode": mode,
        "approve": approve,
        "ssh_port": ssh_port,
        **selection,
        "metadata": redact_object(metadata or {}),
        "created_at": _now(),
    }
    client = _redis(settings)
    queued = {
        "job_id": job_id,
        "status": "queued",
        "created_at": job["created_at"],
        **selection,
    }
    _store(client, settings, job_id, queued)
    client.rpush(settings.agent_queue_name, json.dumps(job, ensure_ascii=False, default=str))
    return {
        **queued,
        "queue": settings.agent_queue_name,
        "worker_pool": settings.agent_worker_name,
    }


def get_job(job_id: str, *, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    value = _redis(settings).get(_result_key(settings, job_id))
    if not value:
        return None
    payload = json.loads(value)
    return payload if isinstance(payload, dict) else None


def _execute_job(job: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
    job_id = str(job["job_id"])
    client = _redis(settings)
    worker = f"{settings.agent_worker_name}@{socket.gethostname()}"
    selection = {
        "provider": str(job.get("provider") or _default_provider(settings)),
        "model": str(job.get("model") or ""),
        "playbook_mode": str(job.get("playbook_mode") or "auto"),
        "playbook_id": job.get("playbook_id"),
    }
    _store(
        client,
        settings,
        job_id,
        {
            "job_id": job_id,
            "status": "running",
            "worker": worker,
            "started_at": _now(),
            **selection,
        },
    )
    try:
        environment = EnvironmentType(job.get("environment") or EnvironmentType.UNKNOWN.value)
        result = run_target(
            str(job["reference"]),
            str(job.get("objective") or ""),
            environment=environment,
            mode=str(job.get("mode") or "propose"),
            approve=bool(job.get("approve", False)),
            ssh_port=job.get("ssh_port"),
            provider_name=selection["provider"],
            model_name=selection["model"] or None,
            playbook_mode=selection["playbook_mode"],
            playbook_id=selection["playbook_id"],
            settings=settings,
        )
        payload = {
            "job_id": job_id,
            "status": "completed",
            "worker": worker,
            "completed_at": _now(),
            "investigation_id": result.get("investigation_id"),
            "result": result,
            **selection,
        }
        _store(client, settings, job_id, payload)
        return payload
    except Exception as exc:
        payload = {
            "job_id": job_id,
            "status": "failed",
            "worker": worker,
            "completed_at": _now(),
            "error": f"{type(exc).__name__}: {exc}",
            **selection,
        }
        _store(client, settings, job_id, payload)
        return payload


def run_worker_once(
    *,
    settings: Settings | None = None,
    block_seconds: int | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    timeout = settings.agent_queue_block_seconds if block_seconds is None else block_seconds
    item = _redis(settings).blpop(settings.agent_queue_name, timeout=max(0, int(timeout)))
    if not item:
        return None
    _, raw = item
    try:
        job = json.loads(raw)
        if not isinstance(job, dict) or not job.get("job_id"):
            raise JobError("job inválido")
    except Exception as exc:
        raise JobError(f"não foi possível decodificar o job: {exc}") from exc
    return _execute_job(job, settings=settings)


def worker_loop(*, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    while True:
        try:
            run_worker_once(settings=settings)
        except KeyboardInterrupt:
            return
        except Exception:
            time.sleep(2)
