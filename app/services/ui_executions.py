from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from app.services.progress import use_progress


_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-ui-execution")
_LOCK = RLock()
_RECORDS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_TTL = timedelta(hours=24)
_MAX_RECORDS = 200
_STAGE_PERCENT = {
    "queued": 0,
    "execution_started": 4,
    "provider_validation": 10,
    "provider_selected": 18,
    "target_resolution": 24,
    "target_resolved": 30,
    "ssh_connection": 36,
    "ssh_connected": 42,
    "evidence_analysis": 55,
    "queue_submission": 18,
    "queue_wait": 52,
    "result_persistence": 92,
    "completed": 100,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _prune() -> None:
    threshold = _now() - _TTL
    expired = [
        execution_id
        for execution_id, record in _RECORDS.items()
        if datetime.fromisoformat(record["updated_at"]) < threshold
    ]
    for execution_id in expired:
        _RECORDS.pop(execution_id, None)
    while len(_RECORDS) > _MAX_RECORDS:
        _RECORDS.popitem(last=False)


def _bounded_percent(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(0, min(100, number))


def _phase(execution_id: str, event: dict[str, Any]) -> None:
    with _LOCK:
        record = _RECORDS.get(execution_id)
        if not record:
            return
        stage = str(event.get("stage") or "processing")
        fallback = _STAGE_PERCENT.get(stage, int(record.get("percent") or 0))
        supplied = _bounded_percent(event.get("percent"), fallback)
        percent = max(int(record.get("percent") or 0), supplied)
        phase = {
            "stage": stage,
            "status": str(event.get("status") or "running"),
            "detail": str(event.get("detail") or ""),
            "percent": percent,
            "updated_at": _iso(),
            **{
                key: value
                for key, value in event.items()
                if key not in {"stage", "status", "detail", "percent"}
            },
        }
        phases = record["phases"]
        current_index = next(
            (index for index, item in enumerate(phases) if item.get("stage") == phase["stage"]),
            None,
        )
        if current_index is None:
            phases.append(phase)
        else:
            phases[current_index] = {**phases[current_index], **phase}
        record["percent"] = percent
        record["current_phase"] = phase
        record["updated_at"] = phase["updated_at"]


def _execute(execution_id: str, operation: Callable[[], dict[str, Any]]) -> None:
    with _LOCK:
        record = _RECORDS.get(execution_id)
        if not record:
            return
        record["status"] = "running"
        record["started_at"] = _iso()
        record["updated_at"] = record["started_at"]
    _phase(
        execution_id,
        {
            "stage": "execution_started",
            "status": "completed",
            "detail": "Execução recebida pelo Agent IA.",
            "percent": 4,
        },
    )

    try:
        with use_progress(lambda event: _phase(execution_id, event)):
            result = operation()
        _phase(
            execution_id,
            {
                "stage": "completed",
                "status": "completed",
                "detail": "Investigação concluída e disponível para revisão.",
                "percent": 100,
            },
        )
        with _LOCK:
            record = _RECORDS.get(execution_id)
            if not record:
                return
            record["status"] = "completed"
            record["percent"] = 100
            record["result"] = result
            record["completed_at"] = _iso()
            record["updated_at"] = record["completed_at"]
    except Exception as exc:
        _phase(
            execution_id,
            {
                "stage": "failed",
                "status": "failed",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        )
        with _LOCK:
            record = _RECORDS.get(execution_id)
            if not record:
                return
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["completed_at"] = _iso()
            record["updated_at"] = record["completed_at"]


def submit_ui_execution(
    operation: Callable[[], dict[str, Any]],
    *,
    target: str,
    objective: str,
    provider: str,
    model: str | None,
    execution_mode: str,
) -> dict[str, Any]:
    execution_id = str(uuid4())
    created_at = _iso()
    record = {
        "execution_id": execution_id,
        "status": "queued",
        "percent": 0,
        "target": target,
        "objective": objective,
        "provider": provider,
        "model": model,
        "execution_mode": execution_mode,
        "created_at": created_at,
        "updated_at": created_at,
        "started_at": None,
        "completed_at": None,
        "current_phase": {
            "stage": "queued",
            "status": "running",
            "detail": "Aguardando início da investigação.",
            "percent": 0,
            "updated_at": created_at,
        },
        "phases": [],
        "result": None,
        "error": None,
    }
    with _LOCK:
        _prune()
        _RECORDS[execution_id] = record
    _EXECUTOR.submit(_execute, execution_id, operation)
    return execution_detail(execution_id) or {"execution_id": execution_id, "status": "queued", "percent": 0}


def execution_detail(execution_id: str) -> dict[str, Any] | None:
    with _LOCK:
        _prune()
        record = _RECORDS.get(str(execution_id))
        return deepcopy(record) if record else None
