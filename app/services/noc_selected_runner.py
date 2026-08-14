from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from redis import Redis

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services import checkmk_master_patrol as patrol
from app.services import noc_incidents as incident_store
from app.services.checkmk_operational import collect_checkmk_operational_snapshot
from app.services.jobs import cancel_job, enqueue_investigation, get_job
from app.services.noc_action_policy import classify_problem_category
from app.services.noc_autonomy_control import (
    complete_selected_run,
    next_selected_run,
    requeue_selected_run,
    scope_matches_problem,
)
from app.services.noc_incidents import attach_job, incident_objective
from app.services.noc_skills import build_skill_objective
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


def _environment(value: Any) -> EnvironmentType:
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _mark_manual_correction_intent(
    incident_id: str,
    *,
    run_id: str,
    operator: str,
    settings: Settings,
) -> None:
    if not incident_id:
        return
    client = incident_store._redis(settings)
    incident = incident_store._load(client, settings, incident_id)
    if not incident:
        return
    incident.update(
        {
            "manual_correction_requested": True,
            "manual_correction_run_id": run_id,
            "manual_correction_requested_by": operator,
            "manual_correction_requested_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    incident_store._store(client, settings, incident)


def _ensure_manual_job(
    item: dict[str, Any],
    result: dict[str, Any],
    *,
    run_id: str,
    scope: dict[str, Any],
    operator: str,
    settings: Settings,
) -> dict[str, Any]:
    """Transforma o clique em Arrumar em uma intenção real de correção.

    A coleta continua em modo de proposta para que a IA produza ações
    estruturadas, a segunda IA revise e o token fique ligado exatamente às
    ações. O pós-processamento consome essa autorização e executa a correção
    segura automaticamente, sem parar no diagnóstico.
    """

    authorization = dict(result.get("authorization") or {})
    route = dict(result.get("route") or {})
    event = dict(result.get("event") or {})
    incident = dict(event.get("incident") or {})
    if not authorization.get("allowed") or not route.get("valid") or not route.get("auto_investigate") or not incident:
        return result

    incident_id = str(incident.get("id") or "")
    _mark_manual_correction_intent(
        incident_id,
        run_id=run_id,
        operator=operator,
        settings=settings,
    )

    # A ronda pode ter acabado de criar um job genérico de investigação. Para
    # execução manual, substituímos o job ainda enfileirado por outro que carrega
    # explicitamente a intenção de corrigir. Se ele já começou, apenas o
    # acompanhamos para evitar SSH duplicado; o incidente já guarda a intenção.
    previous_job_id = str((result.get("job") or {}).get("job_id") or incident.get("job_id") or "").strip()
    if previous_job_id:
        current = get_job(previous_job_id, settings=settings) or {}
        current_status = str(current.get("status") or "")
        if current_status in {"running", "cancelling"}:
            return {
                **result,
                "queued": True,
                "job": {
                    "job_id": previous_job_id,
                    "created_at": current.get("created_at") or current.get("started_at"),
                },
                "manual_reused_running_job": True,
            }
        if current_status == "queued":
            cancel_job(previous_job_id, settings=settings)

    item = dict(item)
    item["policy_category"] = classify_problem_category(item)
    skill = dict(route.get("skill") or {})
    objective = incident_objective(incident) + "\n\n" + build_skill_objective(
        item,
        skill,
        site_id=str(route.get("site_id") or item.get("site_id") or ""),
        client_alias=str(route.get("client_alias") or item.get("alias") or ""),
    )
    objective += (
        "\n\nINTENÇÃO DO OPERADOR: ARRUMAR ESTE PROBLEMA. "
        "Não encerre o trabalho somente com diagnóstico. Identifique a causa, produza a ação corretiva estruturada "
        "permitida pelo playbook/Skill, submeta-a à revisão da segunda IA e prepare a pós-validação. "
        "Reboot/shutdown do servidor, acesso a banco, ações destrutivas e lifecycle de containers permanecem proibidos."
    )
    playbook_id = str(skill.get("playbook_id") or "").strip() or None
    queued = enqueue_investigation(
        str(route.get("entry_address") or ""),
        objective,
        environment=_environment(route.get("environment")),
        mode="propose",
        approve=False,
        ssh_port=22,
        playbook_mode="manual" if playbook_id else "auto",
        playbook_id=playbook_id,
        metadata={
            "source": "checkmk_master",
            "site_scope": True,
            "noc_incident_id": incident_id,
            "noc_control_revision": scope.get("revision"),
            "noc_run_id": run_id,
            "noc_scope_mode": "selected",
            "checkmk_problem_key": item.get("problem_key"),
            "policy_category": item.get("policy_category"),
            "site_id": route.get("site_id"),
            "client_alias": route.get("client_alias"),
            "entry_address": route.get("entry_address"),
            "livestatus_port": route.get("livestatus_port"),
            "status_host": route.get("status_host"),
            "internal_target": route.get("internal_address"),
            "checkmk_host": item.get("host"),
            "checkmk_address": item.get("host_address"),
            "service": item.get("service"),
            "state": item.get("state_name"),
            "target_strategy": route.get("strategy"),
            "host_kind": route.get("host_kind"),
            "scope_key": route.get("scope_key"),
            "skill": skill,
            "manual_selected": True,
            "manual_correction_requested": True,
            "manual_correction_requested_by": operator,
            "isolation": {
                "site_id": route.get("site_id"),
                "cross_site_internal_ip_lookup": False,
                "reuse_other_customer_session": False,
            },
        },
        settings=settings,
    )
    if incident_id:
        attach_job(incident_id, str(queued["job_id"]), settings=settings)
    return {**result, "queued": True, "job": queued, "manual_forced": True}


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
        operator = str(run.get("requested_by") or "operator")
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
                result = _ensure_manual_job(
                    item,
                    result,
                    run_id=run_id,
                    scope=scope,
                    operator=operator,
                    settings=settings,
                )
                patrol._persist_automation_result(item, result)  # noqa: SLF001
                job = dict(result.get("job") or {})
                event = dict(result.get("event") or {})
                incident = dict(event.get("incident") or {})
                if result.get("queued") and job.get("job_id"):
                    jobs.append(
                        {
                            "job_id": str(job.get("job_id")),
                            "incident_id": str(incident.get("id") or ""),
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
            "intent": "correct_and_validate",
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
