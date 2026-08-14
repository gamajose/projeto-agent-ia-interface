from __future__ import annotations

import json
from typing import Any

from redis import Redis

from app.core.settings import Settings
from app.services.jobs import get_job


_TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _prefix(settings: Settings) -> str:
    return str(getattr(settings, "noc_incident_prefix", "agent-ia:noc") or "agent-ia:noc").rstrip(":")


def _pending_runs_key(settings: Settings) -> str:
    return f"{_prefix(settings)}:autonomy:runs:pending"


def _pending_position(run_id: str, settings: Settings) -> int | None:
    try:
        items = _redis(settings).lrange(_pending_runs_key(settings), 0, -1)
    except Exception:
        return None
    for index, value in enumerate(items, start=1):
        if str(value) == str(run_id):
            return index
    return None


def _job_queue_position(job_id: str, settings: Settings) -> int | None:
    try:
        items = _redis(settings).lrange(settings.agent_queue_name, 0, -1)
    except Exception:
        return None
    for index, raw in enumerate(items, start=1):
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict) and str(payload.get("job_id") or "") == str(job_id):
            return index
    return None


def _job_view(item: dict[str, Any], settings: Settings) -> dict[str, Any]:
    job_id = str(item.get("job_id") or "").strip()
    current = get_job(job_id, settings=settings) if job_id else None
    current = current or {}
    phase = dict(current.get("current_phase") or {})
    status = str(current.get("status") or "queued")
    return {
        **item,
        "job_id": job_id,
        "status": status,
        "percent": max(0, min(100, int(current.get("percent") or 0))),
        "queue_position": _job_queue_position(job_id, settings) if status == "queued" else None,
        "phase": str(phase.get("stage") or "worker_wait"),
        "detail": str(phase.get("detail") or ("Aguardando worker operacional disponível." if status == "queued" else "")),
        "updated_at": current.get("updated_at") or current.get("started_at") or item.get("created_at"),
        "investigation_id": current.get("investigation_id"),
        "error": current.get("error"),
    }


def enrich_selected_run(run: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
    payload = dict(run)
    result = dict(payload.get("result") or {})
    descriptors = [dict(item) for item in result.get("jobs") or [] if isinstance(item, dict)]
    jobs = [_job_view(item, settings) for item in descriptors]

    if not jobs:
        payload["queue_position"] = _pending_position(str(payload.get("id") or ""), settings)
        payload["jobs"] = []
        payload["progress"] = {
            "total": 0,
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "percent": 0,
        }
        return payload

    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for job in jobs:
        status = str(job.get("status") or "queued")
        if status in counts:
            counts[status] += 1
        elif status == "cancelling":
            counts["running"] += 1
        else:
            counts["running"] += 1

    total = len(jobs)
    percent = round(sum(int(job.get("percent") or 0) for job in jobs) / max(1, total))
    terminal = all(str(job.get("status") or "") in _TERMINAL_JOB_STATES for job in jobs)
    if terminal:
        if counts["failed"]:
            aggregate_status = "failed"
        elif counts["cancelled"] and counts["completed"] == 0:
            aggregate_status = "cancelled"
        else:
            aggregate_status = "completed"
    elif counts["running"]:
        aggregate_status = "running"
    else:
        aggregate_status = "queued"

    payload["status"] = aggregate_status
    payload["jobs"] = jobs
    payload["progress"] = {"total": total, **counts, "percent": percent}
    payload["queue_position"] = None
    return payload
