from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from redis import Redis

from app.core.settings import Settings, get_settings
from app.services.redaction import redact_text


_OK_STATES = {"0", "OK", "UP", "RECOVERY", "RECOVERED"}
_WARNING_STATES = {"1", "WARN", "WARNING"}
_CRITICAL_STATES = {"2", "CRIT", "CRITICAL", "DOWN"}
_UNKNOWN_STATES = {"3", "UNKNOWN", "UNREACHABLE"}


class NocIncidentError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _prefix(settings: Settings) -> str:
    return str(getattr(settings, "noc_incident_prefix", "agent-ia:noc") or "agent-ia:noc").rstrip(":")


def _ttl(settings: Settings) -> int:
    return max(3600, int(getattr(settings, "noc_incident_ttl_seconds", 604800) or 604800))


def _incident_key(settings: Settings, incident_id: str) -> str:
    return f"{_prefix(settings)}:incident:{incident_id}"


def _event_key(settings: Settings, incident_id: str) -> str:
    return f"{_prefix(settings)}:incident:{incident_id}:events"


def _fingerprint_key(settings: Settings, fingerprint: str) -> str:
    return f"{_prefix(settings)}:fingerprint:{fingerprint}"


def _fingerprint_event_key(settings: Settings, fingerprint: str) -> str:
    return f"{_prefix(settings)}:fingerprint:{fingerprint}:events"


def _open_key(settings: Settings) -> str:
    return f"{_prefix(settings)}:index:open"


def _all_key(settings: Settings) -> str:
    return f"{_prefix(settings)}:index:all"


def _lock_key(settings: Settings, fingerprint: str) -> str:
    return f"{_prefix(settings)}:lock:{fingerprint}"


def normalize_checkmk_state(state: str) -> dict[str, str]:
    raw = str(state or "").strip().upper()
    if raw in _OK_STATES:
        return {"raw": raw or "OK", "kind": "ok", "severity": "healthy"}
    if raw in _WARNING_STATES:
        return {"raw": raw, "kind": "problem", "severity": "attention"}
    if raw in _CRITICAL_STATES:
        return {"raw": raw, "kind": "problem", "severity": "critical"}
    if raw in _UNKNOWN_STATES:
        return {"raw": raw, "kind": "problem", "severity": "inconclusive"}
    return {"raw": raw or "UNKNOWN", "kind": "problem", "severity": "inconclusive"}


def incident_fingerprint(*, site: str | None, host: str, service: str) -> str:
    material = "|".join(
        [
            str(site or "").strip().casefold(),
            str(host or "").strip().casefold(),
            str(service or "").strip().casefold(),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _decode(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _decode_events(values: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in values:
        row = _decode(value)
        if row:
            rows.append(row)
    return rows


def _recent_transition_count(events: list[dict[str, Any]], *, since: datetime) -> int:
    relevant: list[dict[str, Any]] = []
    for event in events:
        try:
            timestamp = datetime.fromisoformat(str(event.get("timestamp") or ""))
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if timestamp >= since:
            relevant.append(event)
    transitions = 0
    previous: str | None = None
    for event in relevant:
        kind = str(event.get("kind") or "")
        if previous is not None and kind and kind != previous:
            transitions += 1
        if kind:
            previous = kind
    return transitions


def _append_event(
    client: Redis,
    settings: Settings,
    *,
    incident_id: str | None,
    fingerprint: str,
    event: dict[str, Any],
) -> None:
    encoded = json.dumps(event, ensure_ascii=False, default=str)
    fingerprint_key = _fingerprint_event_key(settings, fingerprint)
    client.rpush(fingerprint_key, encoded)
    client.ltrim(fingerprint_key, -200, -1)
    client.expire(fingerprint_key, _ttl(settings))
    if incident_id:
        incident_key = _event_key(settings, incident_id)
        client.rpush(incident_key, encoded)
        client.ltrim(incident_key, -200, -1)
        client.expire(incident_key, _ttl(settings))


def _store(client: Redis, settings: Settings, incident: dict[str, Any]) -> dict[str, Any]:
    incident_id = str(incident["id"])
    updated = _now()
    incident["updated_at"] = updated.isoformat()
    client.setex(
        _incident_key(settings, incident_id),
        _ttl(settings),
        json.dumps(incident, ensure_ascii=False, default=str),
    )
    client.zadd(_all_key(settings), {incident_id: updated.timestamp()})
    if incident.get("status") == "resolved":
        client.srem(_open_key(settings), incident_id)
    else:
        client.sadd(_open_key(settings), incident_id)
    return incident


def _load(client: Redis, settings: Settings, incident_id: str) -> dict[str, Any] | None:
    return _decode(client.get(_incident_key(settings, incident_id)))


def _flapping_status(client: Redis, settings: Settings, fingerprint: str) -> tuple[bool, int]:
    window_seconds = max(60, int(getattr(settings, "noc_flapping_window_seconds", 1800) or 1800))
    threshold = max(2, int(getattr(settings, "noc_flapping_transition_threshold", 4) or 4))
    events = _decode_events(client.lrange(_fingerprint_event_key(settings, fingerprint), -100, -1))
    transitions = _recent_transition_count(events, since=_now() - timedelta(seconds=window_seconds))
    return transitions >= threshold, transitions


def incident_objective(incident: dict[str, Any]) -> str:
    flapping = ""
    if incident.get("flapping"):
        flapping = (
            f" Flapping detectado: {incident.get('recent_transition_count', 0)} transições recentes entre normal e problema."
        )
    return (
        "Gerenciar incidente NOC recebido automaticamente do Checkmk. "
        f"Host: {incident.get('host')}. Serviço: {incident.get('service')}. "
        f"Estado atual: {incident.get('current_state')}. Site: {incident.get('site') or 'não informado'}."
        f"{flapping} Saída atual: {incident.get('last_output') or 'sem saída'}. "
        "Investigue com evidências atuais, identifique a causa provável sem presumir, correlacione rede/serviço/monitoramento quando necessário, "
        "proponha somente ações permitidas pelas políticas e deixe explícito o que precisa de aprovação humana."
    )


def register_checkmk_event(
    *,
    host: str,
    service: str,
    state: str,
    output: str,
    site: str | None,
    environment: str,
    requested_auto_correct: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not bool(getattr(settings, "noc_incident_enabled", True)):
        return {"action": "disabled", "should_investigate": True, "incident": None}

    normalized = normalize_checkmk_state(state)
    fingerprint = incident_fingerprint(site=site, host=host, service=service)
    client = _redis(settings)
    now = _now()
    safe_output = redact_text(str(output or ""))[-12000:]
    event = {
        "timestamp": now.isoformat(),
        "state": normalized["raw"],
        "kind": normalized["kind"],
        "severity": normalized["severity"],
        "output": safe_output,
        "source": "checkmk",
    }

    with client.lock(_lock_key(settings, fingerprint), timeout=10, blocking_timeout=5):
        active_id = client.get(_fingerprint_key(settings, fingerprint))
        active = _load(client, settings, active_id) if active_id else None

        if normalized["kind"] == "ok":
            _append_event(client, settings, incident_id=active_id if active else None, fingerprint=fingerprint, event=event)
            if not active:
                return {
                    "action": "recovery_without_open_incident",
                    "should_investigate": False,
                    "incident": None,
                    "state": normalized,
                }
            previous_kind = str(active.get("state_kind") or "problem")
            if previous_kind != "ok":
                active["transition_count"] = int(active.get("transition_count") or 0) + 1
            active.update(
                {
                    "status": "resolved",
                    "current_state": normalized["raw"],
                    "state_kind": normalized["kind"],
                    "severity": normalized["severity"],
                    "last_output": safe_output,
                    "last_seen_at": now.isoformat(),
                    "resolved_at": now.isoformat(),
                    "resolution_source": "checkmk_recovery",
                    "event_count": int(active.get("event_count") or 0) + 1,
                }
            )
            flapping, transitions = _flapping_status(client, settings, fingerprint)
            active["flapping"] = flapping
            active["recent_transition_count"] = transitions
            _store(client, settings, active)
            if client.get(_fingerprint_key(settings, fingerprint)) == str(active["id"]):
                client.delete(_fingerprint_key(settings, fingerprint))
            return {"action": "resolved", "should_investigate": False, "incident": active, "state": normalized}

        if active:
            last_state = str(active.get("current_state") or "")
            last_seen_raw = str(active.get("last_seen_at") or "")
            try:
                last_seen = datetime.fromisoformat(last_seen_raw)
            except ValueError:
                last_seen = now - timedelta(days=1)
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            dedup_seconds = max(0, int(getattr(settings, "noc_incident_dedup_seconds", 300) or 300))
            duplicate = last_state == normalized["raw"] and (now - last_seen).total_seconds() <= dedup_seconds
            if str(active.get("state_kind") or "problem") != normalized["kind"]:
                active["transition_count"] = int(active.get("transition_count") or 0) + 1
            active.update(
                {
                    "current_state": normalized["raw"],
                    "state_kind": normalized["kind"],
                    "severity": normalized["severity"],
                    "last_output": safe_output,
                    "last_seen_at": now.isoformat(),
                    "occurrence_count": int(active.get("occurrence_count") or 0) + 1,
                    "event_count": int(active.get("event_count") or 0) + 1,
                }
            )
            _append_event(client, settings, incident_id=str(active["id"]), fingerprint=fingerprint, event={**event, "deduplicated": duplicate})
            flapping, transitions = _flapping_status(client, settings, fingerprint)
            active["flapping"] = flapping
            active["recent_transition_count"] = transitions
            _store(client, settings, active)
            return {
                "action": "deduplicated" if duplicate else "updated",
                "should_investigate": False,
                "incident": active,
                "state": normalized,
            }

        incident_id = str(uuid.uuid4())
        _append_event(client, settings, incident_id=incident_id, fingerprint=fingerprint, event=event)
        flapping, transitions = _flapping_status(client, settings, fingerprint)
        incident = {
            "id": incident_id,
            "fingerprint": fingerprint,
            "host": str(host).strip(),
            "service": str(service).strip(),
            "site": str(site).strip() if site else None,
            "environment": str(environment or "unknown"),
            "status": "new",
            "current_state": normalized["raw"],
            "state_kind": normalized["kind"],
            "severity": normalized["severity"],
            "last_output": safe_output,
            "first_seen_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "resolved_at": None,
            "resolution_source": None,
            "occurrence_count": 1,
            "event_count": 1,
            "transition_count": 0,
            "recent_transition_count": transitions,
            "flapping": flapping,
            "job_id": None,
            "investigation_id": None,
            "analysis_status": None,
            "confidence": 0,
            "probable_cause": None,
            "conclusion": None,
            "approval_available": False,
            "requested_auto_correct": bool(requested_auto_correct),
            "acknowledged_by": None,
            "acknowledged_at": None,
            "attention_reason": None,
            "created_at": now.isoformat(),
        }
        _store(client, settings, incident)
        client.setex(_fingerprint_key(settings, fingerprint), _ttl(settings), incident_id)
        return {
            "action": "created",
            "should_investigate": bool(getattr(settings, "noc_auto_investigate", True)),
            "incident": incident,
            "state": normalized,
        }


def attach_job(incident_id: str, job_id: str, *, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    incident = _load(client, settings, incident_id)
    if not incident:
        return None
    incident.update({"job_id": job_id, "status": "queued", "attention_reason": None})
    return _store(client, settings, incident)


def apply_investigation_result(
    incident_id: str,
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    incident = _load(client, settings, incident_id)
    if not incident:
        return None
    analysis = dict(result.get("analysis") or {})
    corrections = list(result.get("corrections") or [])
    approval_available = bool(result.get("approval_token"))
    validated_correction = any(str(item.get("status") or "") == "validated" for item in corrections)
    analysis_status = str(analysis.get("status") or "inconclusive")
    if incident.get("status") == "resolved":
        workflow_status = "resolved"
    elif approval_available:
        workflow_status = "awaiting_approval"
    elif validated_correction or analysis_status == "healthy":
        workflow_status = "watching"
    else:
        workflow_status = "needs_attention"
    incident.update(
        {
            "status": workflow_status,
            "investigation_id": result.get("investigation_id"),
            "analysis_status": analysis_status,
            "confidence": int(analysis.get("confidence") or 0),
            "probable_cause": analysis.get("probable_cause"),
            "conclusion": analysis.get("conclusion"),
            "approval_available": approval_available,
            "attention_reason": None if workflow_status in {"watching", "awaiting_approval", "resolved"} else analysis.get("conclusion") or "A investigação precisa de intervenção do operador.",
        }
    )
    return _store(client, settings, incident)


def sync_incident_job(incident_id: str, *, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    incident = _load(client, settings, incident_id)
    if not incident or not incident.get("job_id") or incident.get("status") == "resolved":
        return incident
    from app.services.jobs import get_job

    job = get_job(str(incident["job_id"]), settings=settings)
    if not job:
        return incident
    status = str(job.get("status") or "")
    if status == "queued":
        incident["status"] = "queued"
        return _store(client, settings, incident)
    if status in {"running", "cancelling"}:
        incident["status"] = "investigating"
        return _store(client, settings, incident)
    if status in {"failed", "cancelled"}:
        incident["status"] = "needs_attention"
        incident["attention_reason"] = str(job.get("error") or "A investigação automática não foi concluída.")
        return _store(client, settings, incident)
    if status == "completed" and isinstance(job.get("result"), dict):
        return apply_investigation_result(incident_id, job["result"], settings=settings)
    return incident


def get_noc_incident(
    incident_id: str,
    *,
    include_events: bool = True,
    sync_job: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    incident = sync_incident_job(incident_id, settings=settings) if sync_job else _load(client, settings, incident_id)
    if not incident:
        return None
    result = dict(incident)
    if include_events:
        result["events"] = _decode_events(client.lrange(_event_key(settings, incident_id), -100, -1))
    return result


def list_noc_incidents(
    *,
    limit: int = 50,
    status: str | None = None,
    open_only: bool = False,
    sync_jobs: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    client = _redis(settings)
    candidate_ids = (
        list(client.smembers(_open_key(settings)))
        if open_only
        else list(client.zrevrange(_all_key(settings), 0, max(limit * 4, 100)))
    )
    rows: list[dict[str, Any]] = []
    for incident_id in candidate_ids:
        incident = get_noc_incident(
            str(incident_id),
            include_events=False,
            sync_job=sync_jobs,
            settings=settings,
        )
        if not incident:
            client.srem(_open_key(settings), incident_id)
            client.zrem(_all_key(settings), incident_id)
            continue
        if status and str(incident.get("status") or "") != status:
            continue
        rows.append(incident)
    rows.sort(key=lambda item: str(item.get("last_seen_at") or item.get("created_at") or ""), reverse=True)
    return {"total": len(rows), "items": rows[: max(1, min(limit, 200))]}


def noc_dashboard(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    data = list_noc_incidents(limit=200, sync_jobs=True, settings=settings)
    items = data["items"]
    today = _now().date()
    counts = {
        "active": 0,
        "queued": 0,
        "investigating": 0,
        "awaiting_approval": 0,
        "watching": 0,
        "needs_attention": 0,
        "flapping": 0,
        "resolved_today": 0,
    }
    for item in items:
        status = str(item.get("status") or "")
        if status != "resolved":
            counts["active"] += 1
        if status in counts:
            counts[status] += 1
        if item.get("flapping") and status != "resolved":
            counts["flapping"] += 1
        if status == "resolved" and item.get("resolved_at"):
            try:
                resolved = datetime.fromisoformat(str(item["resolved_at"]))
            except ValueError:
                continue
            if resolved.date() == today:
                counts["resolved_today"] += 1
    return {"counts": counts, "recent": items[:20]}


def acknowledge_incident(
    incident_id: str,
    *,
    operator: str,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    incident = _load(client, settings, incident_id)
    if not incident:
        return None
    incident["acknowledged_by"] = operator.strip() or "Operador Agent IA"
    incident["acknowledged_at"] = _iso()
    return _store(client, settings, incident)


def resolve_incident(
    incident_id: str,
    *,
    operator: str,
    reason: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    incident = _load(client, settings, incident_id)
    if not incident:
        return None
    incident.update(
        {
            "status": "resolved",
            "resolved_at": _iso(),
            "resolution_source": "manual",
            "resolved_by": operator.strip() or "Operador Agent IA",
            "resolution_reason": redact_text(str(reason or ""))[:4000] or None,
        }
    )
    _store(client, settings, incident)
    fingerprint_key = _fingerprint_key(settings, str(incident.get("fingerprint") or ""))
    if client.get(fingerprint_key) == incident_id:
        client.delete(fingerprint_key)
    return incident
