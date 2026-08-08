from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.approved_execution import execute_approved_investigation
from app.services.jobs import enqueue_investigation
from app.services.noc_checkmk_runtime import is_green, query_incident_service
from app.services.noc_communications import build_incident_communications, publish_incident_communications
from app.services import noc_incidents as incident_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _classified_environment(result: dict[str, Any], incident: dict[str, Any]) -> str:
    classification = dict(result.get("environment_classification") or {})
    value = classification.get("environment") or incident.get("environment") or EnvironmentType.UNKNOWN.value
    if hasattr(value, "value"):
        value = value.value
    normalized = str(value or EnvironmentType.UNKNOWN.value).strip().casefold()
    return normalized if normalized in {item.value for item in EnvironmentType} else EnvironmentType.UNKNOWN.value


def _environment_type(value: Any) -> EnvironmentType:
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _allowed_autonomous_tools(settings: Settings) -> set[str]:
    return {
        item.strip()
        for item in str(settings.noc_self_heal_tools or "").split(",")
        if item.strip()
    }


def _incident_update(incident_id: str, changes: dict[str, Any], settings: Settings) -> dict[str, Any] | None:
    client = incident_store._redis(settings)
    incident = incident_store._load(client, settings, incident_id)
    if not incident:
        return None
    incident.update(changes)
    return incident_store._store(client, settings, incident)


def _event(incident: dict[str, Any], event_type: str, data: dict[str, Any], settings: Settings) -> None:
    client = incident_store._redis(settings)
    incident_store._append_event(
        client,
        settings,
        incident_id=str(incident.get("id") or ""),
        fingerprint=str(incident.get("fingerprint") or ""),
        event={
            "timestamp": _now().isoformat(),
            "kind": "workflow",
            "source": "noc_supervisor",
            "event_type": event_type,
            **data,
        },
    )


def _autonomy_eligible(incident: dict[str, Any], result: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    if int(settings.noc_autonomy_level) < 4 or not settings.noc_self_heal_enabled:
        return False, "autonomia L4/self-healing não habilitada"

    environment = _classified_environment(result, incident)
    if environment not in {EnvironmentType.MONITORING.value, EnvironmentType.TRAINING.value}:
        return False, f"ambiente {environment} fora do envelope autônomo"

    analysis = dict(result.get("analysis") or {})
    if int(analysis.get("confidence") or 0) < int(settings.noc_self_heal_min_confidence):
        return False, "confiança abaixo do mínimo para self-healing"

    review = dict(result.get("review") or analysis.get("review") or {})
    if not review.get("approved"):
        return False, "segunda IA não aprovou a correção"
    if not result.get("approval_token"):
        return False, "não há token de aprovação gerado pela investigação"

    proposals = [
        item
        for item in (analysis.get("proposed_actions") or [])
        if isinstance(item, dict) and item.get("status") == "proposed"
    ]
    if not proposals:
        return False, "nenhuma ação corretiva proposta"

    requested = {str(item.get("tool") or "") for item in proposals if str(item.get("tool") or "")}
    allowed = _allowed_autonomous_tools(settings)
    if not requested or not requested.issubset(allowed):
        return False, "a proposta contém ação fora da allowlist autônoma"
    return True, "ações de baixo risco autorizadas para self-healing"


def _auto_execute(
    incident: dict[str, Any],
    result: dict[str, Any],
    settings: Settings,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    eligible, reason = _autonomy_eligible(incident, result, settings)
    if not eligible:
        return None, {"eligible": False, "reason": reason}

    investigation_id = str(result.get("investigation_id") or "")
    token = str(result.get("approval_token") or "")
    if not investigation_id or not token:
        return None, {"eligible": False, "reason": "investigação/token ausentes"}

    rounds: list[dict[str, Any]] = []
    current_token = token
    last_execution: dict[str, Any] | None = None
    max_rounds = max(1, int(settings.noc_autonomy_max_approval_rounds))
    for round_number in range(1, max_rounds + 1):
        execution = execute_approved_investigation(
            investigation_id,
            current_token,
            requested_by="NOC Autopilot L4",
            settings=settings,
        )
        last_execution = execution
        rounds.append(
            {
                "round": round_number,
                "status": execution.get("status"),
                "state": execution.get("state"),
                "new_approval_required": execution.get("new_approval_required"),
                "tools": [str(item.get("tool") or "") for item in (execution.get("results") or [])],
            }
        )
        if not execution.get("new_approval_required"):
            break

        pending = [item for item in (execution.get("pending_actions") or []) if isinstance(item, dict)]
        pending_tools = {str(item.get("tool") or "") for item in pending if str(item.get("tool") or "")}
        if not pending_tools or not pending_tools.issubset(_allowed_autonomous_tools(settings)):
            break
        pending_review = dict(execution.get("pending_review") or {})
        if not pending_review.get("approved"):
            break
        next_token = str(execution.get("next_approval_token") or "")
        if not next_token:
            break
        current_token = next_token

    return last_execution, {"eligible": True, "reason": reason, "rounds": rounds}


def _record_communications(
    incident: dict[str, Any],
    result: dict[str, Any] | None,
    *,
    phase: str,
    settings: Settings,
) -> dict[str, Any]:
    communications = build_incident_communications(
        incident,
        result,
        state=str(incident.get("status") or phase),
        settings=settings,
    )
    delivery = publish_incident_communications(incident, communications, phase=phase, settings=settings)
    history = list(incident.get("communications") or [])
    history.append({"phase": phase, **communications, "delivery": delivery})
    history = history[-20:]
    _incident_update(
        str(incident["id"]),
        {"communications": history, "last_communication_phase": phase},
        settings,
    )
    return {"communications": communications, "delivery": delivery}


def _resolve_from_runtime(
    incident: dict[str, Any],
    runtime: dict[str, Any],
    *,
    source: str,
    settings: Settings,
) -> dict[str, Any]:
    now = _now().isoformat()
    updated = _incident_update(
        str(incident["id"]),
        {
            "status": "resolved",
            "severity": "healthy",
            "current_state": "OK",
            "state_kind": "ok",
            "resolved_at": now,
            "resolution_source": source,
            "last_checkmk_runtime": runtime,
            "attention_reason": None,
        },
        settings,
    ) or incident
    client = incident_store._redis(settings)
    client.srem(incident_store._open_key(settings), str(incident["id"]))
    fingerprint_key = incident_store._fingerprint_key(settings, str(incident.get("fingerprint") or ""))
    if client.get(fingerprint_key) == str(incident["id"]):
        client.delete(fingerprint_key)
    _event(updated, "resolved", {"source": source, "runtime": runtime}, settings)
    _record_communications(updated, None, phase="resolved", settings=settings)
    return updated


def postprocess_investigation_result(
    incident_id: str,
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    incident = incident_store.apply_investigation_result(incident_id, result, settings=settings)
    if not incident:
        return None

    effective_environment = _classified_environment(result, incident)
    incident = _incident_update(
        incident_id,
        {
            "environment": effective_environment,
            "environment_classification": result.get("environment_classification") or {},
        },
        settings,
    ) or incident

    _event(
        incident,
        "investigation_completed",
        {
            "investigation_id": result.get("investigation_id"),
            "analysis_status": (result.get("analysis") or {}).get("status"),
            "confidence": (result.get("analysis") or {}).get("confidence"),
            "environment": effective_environment,
        },
        settings,
    )

    execution: dict[str, Any] | None = None
    autonomy: dict[str, Any]
    try:
        execution, autonomy = _auto_execute(incident, result, settings)
        incident = _incident_update(incident_id, {"autonomy": autonomy}, settings) or incident
    except Exception as exc:
        autonomy = {
            "eligible": True,
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }
        incident = _incident_update(
            incident_id,
            {
                "autonomy": autonomy,
                "status": "needs_attention",
                "attention_reason": "A correção autônoma foi autorizada, mas falhou durante a execução segura.",
            },
            settings,
        ) or incident
        _event(incident, "autonomous_correction_failed", {"reason": autonomy["reason"]}, settings)

    if execution:
        _event(
            incident,
            "autonomous_correction",
            {
                "status": execution.get("status"),
                "state": execution.get("state"),
                "before_after": execution.get("before_after"),
            },
            settings,
        )
        validated = execution.get("status") == "validated"
        incident = _incident_update(
            incident_id,
            {
                "autopilot_execution": execution,
                "status": "watching" if validated else "needs_attention",
                "attention_reason": (
                    None
                    if validated
                    else "Self-healing executado, mas a pós-validação local não confirmou a recuperação."
                ),
                "playbook_draft": execution.get("playbook_draft"),
            },
            settings,
        ) or incident

    if incident.get("status") == "watching":
        runtime = query_incident_service(incident, force=True, settings=settings)
        incident = _incident_update(
            incident_id,
            {
                "last_checkmk_runtime": runtime,
                "watch_started_at": incident.get("watch_started_at") or _now().isoformat(),
                "next_check_at": (_now() + timedelta(seconds=int(settings.noc_watch_interval_seconds))).isoformat(),
                "recheck_count": int(incident.get("recheck_count") or 0) + 1,
            },
            settings,
        ) or incident
        _event(incident, "forced_check", {"runtime": runtime}, settings)
        if is_green(runtime):
            return _resolve_from_runtime(incident, runtime, source="checkmk_forced_recheck", settings=settings)

    if incident.get("status") in {"needs_attention", "awaiting_approval"}:
        phase = "needs_attention" if incident.get("status") == "needs_attention" else "awaiting_approval"
        if incident.get("last_communication_phase") != phase:
            _record_communications(incident, result, phase=phase, settings=settings)
    return incident


def mark_job_failure(
    incident_id: str,
    error: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    incident = _incident_update(
        incident_id,
        {"status": "needs_attention", "attention_reason": error[:2000]},
        settings,
    )
    if incident:
        _event(incident, "investigation_failed", {"error": error[:1000]}, settings)
        if incident.get("last_communication_phase") != "needs_attention":
            _record_communications(incident, None, phase="needs_attention", settings=settings)
    return incident


def _watch_due(incident: dict[str, Any]) -> bool:
    due = _parse_time(incident.get("next_check_at"))
    return due is None or due <= _now()


def _watch_timed_out(incident: dict[str, Any], settings: Settings) -> bool:
    started = _parse_time(incident.get("watch_started_at")) or _parse_time(incident.get("first_seen_at"))
    if not started:
        return False
    return (_now() - started).total_seconds() >= int(settings.noc_watch_timeout_seconds)


def _reinvestigate(incident: dict[str, Any], runtime: dict[str, Any], settings: Settings) -> dict[str, Any]:
    count = int(incident.get("reinvestigation_count") or 0)
    if not settings.noc_reinvestigate_on_watch_failure or count >= int(settings.noc_max_reinvestigations):
        return incident

    objective = (
        f"O incidente NOC continua não verde após correção/validação. Host {incident.get('host')}, serviço {incident.get('service')}. "
        f"Estado atual no Livestatus: {runtime.get('state')} / {runtime.get('status')}. "
        f"Saída: {runtime.get('plugin_output') or incident.get('last_output')}. "
        "Reabra a investigação, compare com a causa anterior, confirme se a correção atacou a causa real e procure outro bloqueio sem repetir ação já falha."
    )
    queued = enqueue_investigation(
        str(incident.get("host") or ""),
        objective,
        environment=_environment_type(incident.get("environment")),
        mode="propose",
        approve=False,
        metadata={
            "source": "noc_reinvestigation",
            "noc_incident_id": incident.get("id"),
            "noc_fingerprint": incident.get("fingerprint"),
            "previous_investigation_id": incident.get("investigation_id"),
            "noc_autonomy_level": settings.noc_autonomy_level,
        },
        settings=settings,
    )
    updated = _incident_update(
        str(incident["id"]),
        {
            "status": "queued",
            "job_id": queued.get("job_id"),
            "reinvestigation_count": count + 1,
            "attention_reason": None,
        },
        settings,
    ) or incident
    _event(updated, "reinvestigation_queued", {"job_id": queued.get("job_id")}, settings)
    return updated


def process_watching_incident(
    incident: dict[str, Any],
    *,
    settings: Settings,
) -> dict[str, Any]:
    if not _watch_due(incident):
        return incident

    runtime = query_incident_service(incident, force=True, settings=settings)
    rechecks = int(incident.get("recheck_count") or 0) + 1
    updated = _incident_update(
        str(incident["id"]),
        {
            "last_checkmk_runtime": runtime,
            "recheck_count": rechecks,
            "next_check_at": (_now() + timedelta(seconds=int(settings.noc_watch_interval_seconds))).isoformat(),
        },
        settings,
    ) or incident
    _event(updated, "watch_recheck", {"runtime": runtime, "recheck_count": rechecks}, settings)

    if is_green(runtime):
        return _resolve_from_runtime(updated, runtime, source="checkmk_watcher", settings=settings)

    if rechecks >= int(settings.noc_watch_max_rechecks) or _watch_timed_out(updated, settings):
        reinvestigated = _reinvestigate(updated, runtime, settings)
        if reinvestigated.get("status") == "queued":
            return reinvestigated
        updated = _incident_update(
            str(updated["id"]),
            {
                "status": "needs_attention",
                "attention_reason": "O serviço permaneceu não-OK após as revalidações automáticas do Checkmk.",
            },
            settings,
        ) or updated
        if updated.get("last_communication_phase") != "needs_attention":
            _record_communications(updated, None, phase="needs_attention", settings=settings)
    return updated


def force_recheck_incident(
    incident_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = incident_store._redis(settings)
    incident = incident_store._load(client, settings, incident_id)
    if not incident:
        return None
    runtime = query_incident_service(incident, force=True, settings=settings)
    incident = _incident_update(
        incident_id,
        {
            "last_checkmk_runtime": runtime,
            "recheck_count": int(incident.get("recheck_count") or 0) + 1,
            "next_check_at": (_now() + timedelta(seconds=int(settings.noc_watch_interval_seconds))).isoformat(),
        },
        settings,
    ) or incident
    _event(incident, "manual_forced_check", {"runtime": runtime}, settings)
    if is_green(runtime):
        return _resolve_from_runtime(incident, runtime, source="operator_forced_recheck", settings=settings)
    return incident


def supervisor_tick(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.noc_incident_enabled:
        return {"enabled": False, "processed": 0}

    data = incident_store.list_noc_incidents(limit=200, open_only=True, sync_jobs=True, settings=settings)
    processed = 0
    errors: list[dict[str, str]] = []
    for incident in data.get("items") or []:
        try:
            status = str(incident.get("status") or "")
            if status == "watching":
                process_watching_incident(incident, settings=settings)
                processed += 1
            elif status == "needs_attention" and incident.get("last_communication_phase") != "needs_attention":
                _record_communications(incident, None, phase="needs_attention", settings=settings)
                processed += 1
        except Exception as exc:
            errors.append(
                {
                    "incident_id": str(incident.get("id") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "enabled": True,
        "autonomy_level": settings.noc_autonomy_level,
        "processed": processed,
        "open": len(data.get("items") or []),
        "errors": errors[-20:],
    }
