from __future__ import annotations

import json
import time
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.cancellation import ExecutionCancelled, raise_if_cancelled
from app.services.evidence_timing import stamp_evidence_timing
from app.services.incident_orchestration import enrich_incident_intelligence
from app.services.investigation_insights import enrich_investigation_result
from app.services.jobs import cancel_job, enqueue_investigation, get_job
from app.services.multi_host_runner import run_multi_host_tracked
from app.services.performance_config import get_performance_config
from app.services.progress import report_progress
from app.services.result_presentation import finalize_result_presentation
from app.services.tracked_runner import persist_result_inventory, run_target_tracked
from app.services.ui_executions import (
    execution_detail,
    execution_event_batch,
    execution_latest_cursor,
    request_execution_cancel,
    submit_ui_execution,
)
from app.web import (
    _compact_result,
    _operator_name,
    _require_access,
    _require_mutation,
    _validate_selection,
)
from app.web_topology import MultiHostInvestigationPayload


router = APIRouter(tags=["interface-executions"])
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


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
    compact["multi_host"] = result.get("multi_host") or (result.get("analysis") or {}).get("multi_host")
    compact["child_investigations"] = result.get("child_investigations") or []
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
def start_ui_execution(payload: MultiHostInvestigationPayload, request: Request) -> dict[str, Any]:
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
    multi_host_options = {
        "customer_name": (payload.customer_name or "").strip() or None,
        "auto_expand_scope": bool(payload.auto_expand_scope),
        "related_targets": [item.model_dump(mode="json") for item in payload.related_targets],
    }
    target = payload.target.strip()
    objective = payload.objective.strip()
    configured_execution_mode = settings.agent_execution_mode.strip().casefold()
    execution_mode = "inline" if payload.multi_host else configured_execution_mode

    if execution_mode == "queue":

        def operation() -> dict[str, Any]:
            raise_if_cancelled("Coleta cancelada antes de entrar na fila.")
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
            try:
                raise_if_cancelled("Coleta cancelada logo após entrar na fila.")
            except ExecutionCancelled:
                cancel_job(job_id, settings=settings)
                raise
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
                current = get_job(job_id, settings=settings)
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
                    stamp_evidence_timing(raw)
                    enrich_investigation_result(raw, settings=settings)
                    enrich_incident_intelligence(raw)
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
            if payload.multi_host:
                raw = run_multi_host_tracked(
                    target,
                    objective,
                    **common,
                    **multi_host_options,
                )
            else:
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


def _sse_payload(event_id: str, event_name: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_name}\ndata: {data}\n\n"


@router.get("/ui/api/executions/{execution_id}/events")
def stream_ui_execution(execution_id: str, request: Request) -> StreamingResponse:
    _require_access(request)
    config = get_performance_config()
    if not config.sse_enabled:
        raise HTTPException(status_code=404, detail="stream de eventos desabilitado")
    initial = execution_detail(execution_id)
    if not initial:
        raise HTTPException(status_code=404, detail="execução não encontrada ou expirada")
    requested_cursor = (
        request.headers.get("last-event-id")
        or request.query_params.get("cursor")
        or execution_latest_cursor(execution_id)
    )

    def generate() -> Iterator[str]:
        cursor = str(requested_cursor or "0")
        yield "retry: 1500\n\n"
        yield _sse_payload(cursor, "snapshot", initial)
        if str(initial.get("status")) in _TERMINAL_STATUSES:
            return
        while True:
            rows, cursor = execution_event_batch(
                execution_id,
                cursor,
                block_milliseconds=config.sse_block_milliseconds,
            )
            if not rows:
                current = execution_detail(execution_id)
                if current and str(current.get("status")) in _TERMINAL_STATUSES:
                    cursor = execution_latest_cursor(execution_id)
                    yield _sse_payload(cursor, "snapshot", current)
                    return
                yield f": heartbeat {int(time.time())}\n\n"
                continue
            for event_id, payload in rows:
                event_name = "snapshot" if payload.get("stage") == "snapshot" else "progress"
                yield _sse_payload(event_id, event_name, payload)
                if event_name == "snapshot":
                    record = payload.get("record") if isinstance(payload.get("record"), dict) else None
                    if record and str(record.get("status")) in _TERMINAL_STATUSES:
                        return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ui/api/executions/{execution_id}/cancel")
def cancel_ui_execution(execution_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    current = execution_detail(execution_id)
    if not current:
        raise HTTPException(status_code=404, detail="execução não encontrada ou expirada")

    job_id = str(current.get("job_id") or "")
    if job_id:
        try:
            remote = cancel_job(job_id, settings=get_settings())
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"não foi possível enviar o cancelamento ao worker: {exc}",
            ) from exc
        if not remote:
            raise HTTPException(status_code=409, detail="o job distribuído não está mais disponível para cancelamento")

    record = request_execution_cancel(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="execução não encontrada ou expirada")
    return execution_detail(execution_id) or record
