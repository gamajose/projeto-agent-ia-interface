from __future__ import annotations

import json
import threading
import time
from typing import Any

from redis import Redis

from app.core.settings import Settings, get_settings
from app.services import checkmk_master_patrol as patrol
from app.services.checkmk_operational import collect_checkmk_operational_snapshot
from app.services.noc_autonomy_control import (
    complete_selected_run,
    next_selected_run,
    requeue_selected_run,
    scope_matches_problem,
)
from app.services.redaction import redact_text


_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _prioritize_jobs(job_ids: list[str], *, settings: Settings) -> None:
    if not job_ids:
        return
    client = _redis(settings)
    queue = settings.agent_queue_name
    try:
        raw_items = list(client.lrange(queue, 0, -1))
    except Exception:
        return

    by_id: dict[str, str] = {}
    for raw in raw_items:
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            job_id = str(payload.get("job_id") or "")
            if job_id in job_ids:
                by_id[job_id] = raw

    selected: list[str] = []
    for job_id in job_ids:
        raw = by_id.get(job_id)
        if not raw:
            continue
        try:
            removed = int(client.lrem(queue, 1, raw) or 0)
        except Exception:
            removed = 0
        if removed:
            selected.append(raw)

    # LPUSH em ordem reversa preserva a ordem original dos itens selecionados.
    for raw in reversed(selected):
        client.lpush(queue, raw)


def process_selected_run_once(*, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    run = next_selected_run(settings=settings)
    if not run:
        return None

    # Coordena com a ronda automática do master dentro do mesmo processo.
    if not patrol._THREAD_LOCK.acquire(blocking=False):  # noqa: SLF001 - lock compartilhado intencionalmente
        requeue_selected_run(run, settings=settings)
        return {"status": "busy", "run_id": run.get("id")}

    try:
        snapshot = collect_checkmk_operational_snapshot(settings=settings)
        if snapshot.get("status") == "busy":
            requeue_selected_run(run, settings=settings)
            return {"status": "busy", "run_id": run.get("id")}
        if snapshot.get("status") != "completed":
            completed = complete_selected_run(run, dict(snapshot), settings=settings)
            return {"status": completed.get("status"), "run_id": completed.get("id"), "result": snapshot}

        scope = dict(run.get("scope") or {})
        run_id = str(run.get("id") or "")
        selected_problems = [
            dict(item)
            for item in snapshot.get("problems") or []
            if isinstance(item, dict) and scope_matches_problem(item, scope)
        ]
        jobs: list[dict[str, Any]] = []
        processing_errors: list[str] = []

        for item in selected_problems:
            try:
                result = patrol._register_problem(  # noqa: SLF001 - reutiliza o pipeline oficial do NOC
                    item,
                    settings=settings,
                    scope_override=scope,
                    run_id=run_id,
                    passive=False,
                )
                patrol._persist_automation_result(item, result)  # noqa: SLF001
                job = dict(result.get("job") or {})
                if result.get("queued") and job.get("job_id"):
                    jobs.append(
                        {
                            "job_id": str(job.get("job_id")),
                            "site_id": str(item.get("site_id") or ""),
                            "client_alias": str(item.get("alias") or item.get("client_alias") or ""),
                            "host": str(item.get("host") or ""),
                            "host_address": str(item.get("host_address") or ""),
                            "service": str(item.get("service") or ""),
                            "state": str(item.get("state_name") or item.get("state") or ""),
                            "problem_key": str(item.get("problem_key") or ""),
                            "created_at": job.get("created_at"),
                        }
                    )
            except Exception as exc:
                processing_errors.append(redact_text(f"{type(exc).__name__}: {exc}")[:600])

        _prioritize_jobs([str(item.get("job_id") or "") for item in jobs], settings=settings)
        result = {
            "status": "completed",
            "mode": "manual_selected",
            "problems_seen": len(selected_problems),
            "jobs_queued": len(jobs),
            "jobs": jobs,
            "processing_errors": processing_errors,
            "sites_ok": int(snapshot.get("sites_ok") or 0),
            "sites_failed": int(snapshot.get("sites_failed") or 0),
            "hosts_seen": int(snapshot.get("hosts_seen") or 0),
        }
        completed = complete_selected_run(run, result, settings=settings)
        return {"status": completed.get("status"), "run_id": completed.get("id"), "result": result}
    except Exception as exc:
        result = {"status": "failed", "error": redact_text(f"{type(exc).__name__}: {exc}")[:1200]}
        completed = complete_selected_run(run, result, settings=settings)
        return {"status": completed.get("status"), "run_id": completed.get("id"), "result": result}
    finally:
        patrol._THREAD_LOCK.release()  # noqa: SLF001


def _loop(settings: Settings) -> None:
    while True:
        try:
            result = process_selected_run_once(settings=settings)
            if not result or result.get("status") == "busy":
                time.sleep(0.35)
        except Exception:
            time.sleep(1.0)


def start_selected_run_processor_background(*, settings: Settings | None = None) -> bool:
    global _THREAD
    settings = settings or get_settings()
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
        _THREAD = threading.Thread(
            target=_loop,
            args=(settings,),
            name="noc-selected-runner",
            daemon=True,
        )
        _THREAD.start()
    return True
