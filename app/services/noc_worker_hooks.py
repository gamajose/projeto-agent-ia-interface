from __future__ import annotations

from typing import Any

from app.core.settings import Settings, get_settings
from app.services.jobs import get_job
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


def reconcile_noc_jobs(*, settings: Settings | None = None) -> dict[str, Any]:
    """Retoma o pós-processamento se o worker reiniciar entre job e supervisor."""
    settings = settings or get_settings()
    rows = list_noc_incidents(limit=200, open_only=True, sync_jobs=False, settings=settings).get("items") or []
    recovered = 0
    errors: list[dict[str, str]] = []
    for incident in rows:
        job_id = str(incident.get("job_id") or "")
        if not job_id:
            continue
        # Toda execução concluída normalmente grava `autonomy` (inclusive quando
        # não é elegível). Sua ausência indica pós-processamento interrompido.
        if "autonomy" in incident:
            continue
        try:
            job = get_job(job_id, settings=settings)
            if not job:
                continue
            status = str(job.get("status") or "")
            if status == "completed" and isinstance(job.get("result"), dict):
                postprocess_investigation_result(
                    str(incident.get("id") or ""),
                    dict(job["result"]),
                    settings=settings,
                )
                recovered += 1
            elif status in {"failed", "cancelled"} and str(incident.get("status") or "") != "needs_attention":
                mark_job_failure(
                    str(incident.get("id") or ""),
                    str(job.get("error") or f"job {status}"),
                    settings=settings,
                )
                recovered += 1
        except Exception as exc:
            errors.append(
                {
                    "incident_id": str(incident.get("id") or ""),
                    "job_id": job_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"recovered": recovered, "errors": errors[-20:]}
