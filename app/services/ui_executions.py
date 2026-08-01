from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from app.services.cancellation import ExecutionCancelled, raise_if_cancelled, use_cancellation
from app.services.execution_store import get_execution_store
from app.services.performance_config import get_performance_config
from app.services.progress import use_progress


_CONFIG = get_performance_config()
_EXECUTOR = ThreadPoolExecutor(
    max_workers=_CONFIG.execution_thread_workers,
    thread_name_prefix="agent-ui-execution",
)
_LOCK = RLock()
_STORE = get_execution_store()
_MAX_EVENTS_IN_SNAPSHOT = min(500, _CONFIG.execution_event_maxlen)
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_STAGE_PERCENT = {
    "queued": 0,
    "execution_started": 4,
    "worker_wait": 8,
    "provider_validation": 10,
    "provider_selected": 18,
    "target_resolution": 24,
    "target_resolved": 30,
    "ssh_connection": 36,
    "ssh_connected": 42,
    "multi_host_scope": 20,
    "multi_host_triage": 44,
    "multi_host_primary": 50,
    "multi_host_handoff": 62,
    "evidence_analysis": 55,
    "command_started": 55,
    "command_output": 65,
    "command_completed": 75,
    "command_cancelled": 75,
    "queue_submission": 6,
    "queue_wait": 8,
    "result_persistence": 92,
    "completed": 100,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _bounded_percent(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(0, min(100, number))


def _append_snapshot_event(record: dict[str, Any], phase: dict[str, Any]) -> dict[str, Any]:
    event = {**phase, "event_id": str(phase.get("event_id") or uuid4())}
    events = record.setdefault("events", [])
    events.append(event)
    if len(events) > _MAX_EVENTS_IN_SNAPSHOT:
        del events[:-_MAX_EVENTS_IN_SNAPSHOT]
    return event


def _save(execution_id: str, record: dict[str, Any]) -> None:
    record["store_backend"] = _STORE.backend_name()
    _STORE.save(execution_id, record)


def _phase(execution_id: str, event: dict[str, Any]) -> None:
    with _LOCK:
        record = _STORE.get(execution_id)
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
            "updated_at": str(event.get("updated_at") or _iso()),
            **{
                key: value
                for key, value in event.items()
                if key not in {"stage", "status", "detail", "percent", "updated_at"}
            },
        }
        phases = record.setdefault("phases", [])
        current_index = next(
            (index for index, item in enumerate(phases) if item.get("stage") == phase["stage"]),
            None,
        )
        if current_index is None:
            phases.append(phase)
        else:
            phases[current_index] = {**phases[current_index], **phase}
        published = _append_snapshot_event(record, phase)
        record["percent"] = percent
        record["current_phase"] = phase
        record["updated_at"] = phase["updated_at"]
        if phase.get("job_id"):
            record["job_id"] = str(phase["job_id"])
        _save(execution_id, record)
        _STORE.append_event(execution_id, published)


def _cancel_requested(execution_id: str) -> bool:
    record = _STORE.get(execution_id) or {}
    return bool(record.get("cancel_requested"))


def _mark_cancelled(execution_id: str, detail: str) -> None:
    record = _STORE.get(execution_id)
    if not record:
        return
    current = dict(record.get("current_phase") or {})
    stage = str(current.get("stage") or "evidence_analysis")
    percent = int(record.get("percent") or 0)
    _phase(
        execution_id,
        {
            "stage": stage,
            "status": "cancelled",
            "detail": detail,
            "percent": percent,
        },
    )
    with _LOCK:
        record = _STORE.get(execution_id)
        if not record:
            return
        record["status"] = "cancelled"
        record["error"] = None
        record["cancelled_at"] = _iso()
        record["completed_at"] = record["cancelled_at"]
        record["updated_at"] = record["cancelled_at"]
        _save(execution_id, record)
        _STORE.append_event(
            execution_id,
            {
                "stage": "snapshot",
                "status": "cancelled",
                "detail": detail,
                "percent": percent,
                "record": deepcopy(record),
            },
        )


def _execute(execution_id: str, operation: Callable[[], dict[str, Any]]) -> None:
    with _LOCK:
        record = _STORE.get(execution_id)
        if not record:
            return
        record["status"] = "running"
        record["started_at"] = _iso()
        record["updated_at"] = record["started_at"]
        _save(execution_id, record)
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
        with use_progress(lambda event: _phase(execution_id, event)), use_cancellation(
            lambda: _cancel_requested(execution_id)
        ):
            raise_if_cancelled()
            result = operation()
            raise_if_cancelled()
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
            record = _STORE.get(execution_id)
            if not record:
                return
            record["status"] = "completed"
            record["percent"] = 100
            record["result"] = result
            record["completed_at"] = _iso()
            record["updated_at"] = record["completed_at"]
            _save(execution_id, record)
            _STORE.append_event(
                execution_id,
                {
                    "stage": "snapshot",
                    "status": "completed",
                    "detail": "Resultado final disponível.",
                    "percent": 100,
                    "record": deepcopy(record),
                },
            )
    except ExecutionCancelled as exc:
        _mark_cancelled(execution_id, str(exc) or "Coleta cancelada pelo operador.")
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
            record = _STORE.get(execution_id)
            if not record:
                return
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["completed_at"] = _iso()
            record["updated_at"] = record["completed_at"]
            _save(execution_id, record)
            _STORE.append_event(
                execution_id,
                {
                    "stage": "snapshot",
                    "status": "failed",
                    "detail": record["error"],
                    "percent": int(record.get("percent") or 0),
                    "record": deepcopy(record),
                },
            )


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
        "cancelled_at": None,
        "cancel_requested": False,
        "job_id": None,
        "current_phase": {
            "stage": "queued",
            "status": "running",
            "detail": "Aguardando início da investigação.",
            "percent": 0,
            "updated_at": created_at,
        },
        "phases": [],
        "events": [],
        "result": None,
        "error": None,
        "store_backend": _STORE.backend_name(),
    }
    with _LOCK:
        _save(execution_id, record)
        queued = _append_snapshot_event(record, record["current_phase"])
        _save(execution_id, record)
        _STORE.append_event(execution_id, queued)
    _EXECUTOR.submit(_execute, execution_id, operation)
    return execution_detail(execution_id) or {
        "execution_id": execution_id,
        "status": "queued",
        "percent": 0,
    }


def request_execution_cancel(execution_id: str) -> dict[str, Any] | None:
    with _LOCK:
        record = _STORE.get(str(execution_id))
        if not record:
            return None
        if record.get("status") in _TERMINAL_STATUSES:
            return deepcopy(record)
        record["cancel_requested"] = True
        record["status"] = "cancelling"
        record["cancel_requested_at"] = _iso()
        current = dict(record.get("current_phase") or {})
        event = {
            "stage": current.get("stage") or "evidence_analysis",
            "status": "cancelling",
            "detail": "Cancelamento solicitado. Encerrando a coleta atual com segurança.",
            "percent": record.get("percent") or 0,
            "job_id": record.get("job_id"),
        }
        _save(str(execution_id), record)
    _phase(str(execution_id), event)
    return execution_detail(str(execution_id))


def execution_detail(execution_id: str) -> dict[str, Any] | None:
    record = _STORE.get(str(execution_id))
    return deepcopy(record) if record else None


def execution_latest_cursor(execution_id: str) -> str:
    return _STORE.latest_cursor(str(execution_id))


def execution_event_batch(
    execution_id: str,
    cursor: str | None,
    *,
    block_milliseconds: int,
) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    return _STORE.read_events(
        str(execution_id),
        cursor,
        block_milliseconds=block_milliseconds,
    )
