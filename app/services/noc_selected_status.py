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


def _incident(incident_id: str, settings: Settings) -> dict[str, Any]:
    if not incident_id:
        return {}
    try:
        raw = _redis(settings).get(f"{_prefix(settings)}:incident:{incident_id}")
        payload = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _job_view(item: dict[str, Any], settings: Settings) -> dict[str, Any]:
    job_id = str(item.get("job_id") or "").strip()
    current = get_job(job_id, settings=settings) if job_id else None
    current = current or {}
    phase = dict(current.get("current_phase") or {})
    status = str(current.get("status") or "queued")
    percent = max(0, min(100, int(current.get("percent") or 0)))
    detail = str(phase.get("detail") or ("Aguardando worker operacional disponível." if status == "queued" else ""))

    incident_id = str(item.get("incident_id") or "")
    incident = _incident(incident_id, settings)
    incident_status = str(incident.get("status") or "")
    resolution_status: str | None = None

    # A fonte de verdade para "Resolvido" é o incidente após a leitura do
    # Checkmk/Livestatus. Um job técnico completed significa somente que o worker
    # terminou aquela etapa; nunca é suficiente para declarar o sensor corrigido.
    if incident_status == "resolved":
        status = "completed"
        percent = 100
        detail = "Problema corrigido e Checkmk confirmou o sensor em OK."
        resolution_status = "resolved"
    elif incident_status == "correcting":
        status = "running"
        percent = max(percent, 88)
        detail = "Skill em execução. Aplicando a correção segura no ambiente."
        resolution_status = "correcting"
    elif incident_status == "watching":
        status = "running"
        percent = max(percent, 96)
        detail = "Correção aplicada. Aguardando o Checkmk confirmar o sensor em OK."
        resolution_status = "watching"
    elif incident_status in {"queued", "investigating"}:
        status = "running" if incident_status == "investigating" else "queued"
        resolution_status = incident_status
    elif incident_status in {"needs_attention", "awaiting_approval"} and incident.get("manual_correction_requested"):
        status = "failed"
        percent = 100
        detail = str(incident.get("attention_reason") or "Não foi possível concluir a correção automaticamente.")
        resolution_status = incident_status
    elif status == "completed":
        # Sem incidente confirmado não há como provar recuperação. Tratar como
        # falha observável é preferível a exibir um falso positivo verde.
        status = "failed"
        percent = 100
        resolution_status = "unverified"
        detail = (
            "O worker concluiu a etapa técnica, mas a aplicação não recebeu confirmação de OK do Checkmk. "
            "O problema permanece não resolvido até a revalidação do sensor."
        )

    return {
        **item,
        "job_id": job_id,
        "status": status,
        "percent": percent,
        "queue_position": _job_queue_position(job_id, settings) if status == "queued" else None,
        "phase": str(phase.get("stage") or "worker_wait"),
        "detail": detail,
        "updated_at": current.get("updated_at") or current.get("started_at") or item.get("created_at"),
        "investigation_id": current.get("investigation_id") or incident.get("investigation_id"),
        "incident_status": incident_status or None,
        "resolution_status": resolution_status,
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
