from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.cancellation import ExecutionCancelled, raise_if_cancelled
from app.services.jobs import cancel_job, enqueue_investigation, get_job
from app.services.progress import report_progress
from app.services.result_presentation import finalize_result_presentation
from app.services.tracked_runner import persist_result_inventory, run_target_tracked
from app.services.ui_executions import (
    execution_detail,
    request_execution_cancel,
    submit_ui_execution,
)
from app.web import (
    InvestigationPayload,
    _compact_result,
    _operator_name,
    _require_access,
    _require_mutation,
    _validate_selection,
)


router = APIRouter(tags=["interface-executions"])


def _compact_with_request(
    result: dict[str, Any],
    *,
    requested_mode: str,
    requested_provider: str,
    model: str | None,
) -> dict[str, Any]:
    compact = _compact_result(result)
    compact["requested_mode"] = requested_mode
    compact["requested_provider"] = requested_provider
    compact["selected_provider"] = result.get("selected_provider") or requested_provider
    compact["selected_model"] = result.get("selected_model") or model
    compact["inventory"] = result.get("inventory")
    compact["status"] = (result.get("analysis") or {}).get("status") or result.get("status")
    compact["confidence"] = (result.get("analysis") or {}).get("confidence") or result.get("confidence")
    return compact


def _forward_worker_event(event: dict[str, Any], *, job_id: str, worker: str | None) -> None:
    payload = dict(event)
    stage = str(payload.pop("stage", "evidence_analysis"))
    status = str(payload.pop("status", "running"))
    detail = str(payload.pop("detail", ""))
    payload["job_id"] = job_id
    if worker:
        payload["worker"] = worker
    report_progress(stage, status=status, detail=detail, **payload)


@router.post("/ui/api/executions")
def start_ui_execution(payload: InvestigationPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    ensure_database_schema()
    provider, model, effective_mode = _validate_selection(payload, settings)
    if provider == "auto":
        effective_mode = "propose" if payload.mode == "correct" else payload.mode

    common = {
        "environment": payload.environment,
        "mode": effective_mode,
        "approve": False,
        "ssh_port": payload.ssh_port,
        "provider_name": provider,
        "model_name": model,
        "playbook_mode": "auto" if provider == "auto" else payload.playbook_mode,
        "playbook_id": None if provider == "auto" else (payload.playbook_id or "").strip() or None,
        "settings": settings,
    }
    target = payload.target.strip()
    objective = payload.objective.strip()
    execution_mode = settings.agent_execution_mode.strip().casefold()

    if execution_mode == "queue":
        def operation() -> dict[str, Any]:
            report_progress(
                "queue_submission",
                status="completed",
                detail="Investigação enviada para a fila operacional.",
                percent=6,
            )
            job = enqueue_investigation(
                target,
                objective,
                metadata={
                    "source": "web_ui_tracked",
                    "operator": _operator_name(),
                    "requested_mode": payload.mode,
                    "autopilot": provider == "auto",
                },
                **common,
            )
            job_id = str(job.get("job_id") or "")
            report_progress(
                "worker_wait",
                detail=f"Job {job_id} aguardando worker operacional.",
                job_id=job_id,
                job_status="queued",
                percent=8,
            )
            seen_events: set[str] = set()
            waiting_since = time.monotonic()
            last_wait_report = 0.0
            while True:
                raise_if_cancelled("Coleta cancelada pelo operador enquanto aguardava o worker.")
                current = get_job(job_id)
                if not current:
                    raise RuntimeError("job não encontrado ou expirado")
                status = str(current.get("status") or "queued")
                worker = str(current.get("worker") or "") or None

                for event in current.get("events") or []:
                    event_id = str(event.get("event_id") or "")
                    if event_id and event_id in seen_events:
                        continue
                    if event_id:
                        seen_events.add(event_id)
                    _forward_worker_event(dict(event), job_id=job_id, worker=worker)

                if status == "queued":
                    waited = int(time.monotonic() - waiting_since)
                    if waited == 0 or waited - last_wait_report >= 10:
                        report_progress(
                            "worker_wait",
                            detail=f"Aguardando worker disponível há {waited}s.",
                            job_id=job_id,
                            job_status=status,
                            wait_seconds=waited,
                            percent=8,
                        )
                        last_wait_report = float(waited)
                elif status == "cancelling":
                    report_progress(
                        "evidence_analysis",
                        status="cancelling",
                        detail="Cancelamento enviado ao worker. Aguardando o comando atual encerrar.",
                        job_id=job_id,
                        job_status=status,
                        percent=int(current.get("percent") or 55),
                    )
                elif status == "cancelled":
                    raise ExecutionCancelled(
                        str((current.get("current_phase") or {}).get("detail") or "Coleta cancelada pelo operador.")
                    )
                elif status == "failed":
                    raise RuntimeError(str(current.get("error") or "a execução na fila falhou"))
                elif status == "completed":
                    raw = dict(current.get("result") or {})
                    persist_result_inventory(raw, settings=settings)
                    finalize_result_presentation(raw, settings=settings)
                    return _compact_with_request(
                        raw,
                        requested_mode=payload.mode,
                        requested_provider=provider,
                        model=model,
                    )
                time.sleep(1.0)
    else:
        def operation() -> dict[str, Any]:
            raw = run_target_tracked(target, objective, **common)
            return _compact_with_request(
                raw,
                requested_mode=payload.mode,
                requested_provider=provider,
                model=model,
            )

    return submit_ui_execution(
        operation,
        target=target,
        objective=objective,
        provider=provider,
        model=model,
        execution_mode=execution_mode,
    )


@router.get("/ui/api/executions/{execution_id}")
def get_ui_execution(execution_id: str, request: Request) -> dict[str, Any]:
    _require_access(request)
    record = execution_detail(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="execução não encontrada ou expirada")
    return record


@router.post("/ui/api/executions/{execution_id}/cancel")
def cancel_ui_execution(execution_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    record = request_execution_cancel(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="execução não encontrada ou expirada")
    job_id = str(record.get("job_id") or "")
    if job_id:
        cancel_job(job_id)
    return execution_detail(execution_id) or record
