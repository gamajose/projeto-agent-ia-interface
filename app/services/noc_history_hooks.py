from __future__ import annotations

from typing import Any

from app.core.settings import Settings, get_settings
from app.services.noc_action_policy import record_incident_history
from app.services.noc_worker_hooks import handle_worker_result


def _access_failure(text: str) -> bool:
    value = str(text or "").casefold()
    return any(token in value for token in (
        "ssh", "auth", "permission denied", "timed out", "timeout", "banner", "shell unavailable",
        "target shell", "connection refused", "connection reset", "no route", "unreachable",
    ))


def handle_worker_result_with_history(
    job_result: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    incident = handle_worker_result(job_result, settings=settings)
    if not job_result or not incident:
        return incident

    job_status = str(job_result.get("status") or "")
    error = str(job_result.get("error") or "")
    incident_status = str(incident.get("status") or "")

    if job_status == "cancelled" and job_result.get("blocked_by_autonomy"):
        reason = str(job_result.get("autonomy_reason") or "Atuação autônoma pausada pelo operador.")
        record_incident_history(
            incident,
            status="paused",
            reason=reason,
            metadata={"blocked_by_autonomy": True, "job_status": job_status},
        )
        return incident

    if job_status in {"failed", "cancelled"}:
        status = "access_failed" if _access_failure(error) else "failed"
        record_incident_history(incident, status=status, reason=error or f"job {job_status}")
        return incident

    autonomy = dict(incident.get("autonomy") or {})
    autopilot = dict(incident.get("autopilot_execution") or {})
    if incident_status == "resolved":
        status = "adjusted" if autopilot else "resolved"
        reason = "Correção validada e Checkmk voltou ao estado OK." if autopilot else "Alerta normalizado e confirmado pelo Checkmk."
    elif incident_status == "watching":
        status = "adjusted_validating"
        reason = "Correção executada; aguardando revalidação do Checkmk."
    elif incident_status in {"needs_attention", "awaiting_approval"}:
        reason = str(incident.get("attention_reason") or autonomy.get("reason") or "Intervenção manual necessária.")
        status = "access_failed" if _access_failure(reason) else "manual_required"
    elif autonomy.get("paused"):
        status = "paused"
        reason = str(autonomy.get("reason") or "Atuação autônoma pausada pelo operador.")
    elif autonomy and not autonomy.get("eligible", False):
        status = "manual_required"
        reason = str(autonomy.get("reason") or "Categoria fora da política de correção autônoma.")
    else:
        status = "investigated"
        reason = "Investigação concluída; nenhuma correção autônoma confirmada neste momento."

    record_incident_history(
        incident,
        status=status,
        reason=reason,
        metadata={
            "job_status": job_status,
            "incident_status": incident_status,
            "autonomy": autonomy,
            "autopilot_status": autopilot.get("status"),
        },
    )
    return incident
