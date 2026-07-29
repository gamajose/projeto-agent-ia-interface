from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.jobs import enqueue_investigation, get_job
from app.services.progress import report_progress
from app.services.tracked_runner import persist_result_inventory, run_target_tracked
from app.services.ui_executions import execution_detail, submit_ui_execution
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
    return compact


@router.post("/ui/api/executions")
def start_ui_execution(payload: InvestigationPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    ensure_database_schema()
    provider, model, effective_mode = _validate_selection(payload, settings)

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
                detail="Enviando a investigação para o worker operacional.",
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
                "queue_wait",
                detail=f"Job {job_id} aguardando ou executando no worker.",
                job_id=job_id,
            )
            while True:
                current = get_job(job_id)
                if not current:
                    raise RuntimeError("job não encontrado ou expirado")
                status = str(current.get("status") or "queued")
                report_progress(
                    "queue_wait",
                    status="completed" if status == "completed" else "running",
                    detail=(
                        "Worker concluindo e persistindo o resultado."
                        if status == "running"
                        else "Aguardando worker disponível."
                    ),
                    job_id=job_id,
                    job_status=status,
                )
                if status == "failed":
                    raise RuntimeError(str(current.get("error") or "a execução na fila falhou"))
                if status == "completed":
                    raw = dict(current.get("result") or {})
                    persist_result_inventory(raw)
                    return _compact_with_request(
                        raw,
                        requested_mode=payload.mode,
                        requested_provider=provider,
                        model=model,
                    )
                time.sleep(1.5)
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
