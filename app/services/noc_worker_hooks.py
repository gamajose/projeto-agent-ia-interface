from __future__ import annotations

from typing import Any

from app.core.settings import Settings, get_settings
from app.services.noc_incidents import list_noc_incidents
from app.services.noc_supervisor import mark_job_failure, postprocess_investigation_result


def _incident_for_job(job_id: str, settings: Settings) -> dict[str, Any] | None:
    rows = list_noc_incidents(limit=200, open_only=True, sync_jobs=False, settings=settings).get("items") or []
    return next((item for item in rows if str(item.get("job_id") or "") == job_id), None)


def handle_worker_result(
    job_result: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    if not job_result:
        return None
    settings = settings or get_settings()
    job_id = str(job_result.get("job_id") or "")
    if not job_id:
        return None
    incident = _incident_for_job(job_id, settings)
    if not incident:
        return None
    incident_id = str(incident.get("id") or "")
    status = str(job_result.get("status") or "")
    if status == "completed" and isinstance(job_result.get("result"), dict):
        return postprocess_investigation_result(incident_id, dict(job_result["result"]), settings=settings)
    if status in {"failed", "cancelled"}:
        reason = str(job_result.get("error") or f"job {status}")
        return mark_job_failure(incident_id, reason, settings=settings)
    return incident
