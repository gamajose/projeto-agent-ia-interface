from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.settings import Settings, get_settings
from app.services.jobs import get_job
from app.services import noc_incidents as incident_store
from app.services.noc_incidents import list_noc_incidents
from app.services.noc_job_guard import job_runtime_authorization
from app.services.noc_supervisor import mark_job_failure, postprocess_investigation_result


def _incident_for_job(job_id: str, settings: Settings) -> dict[str, Any] | None:
    """Localiza o incidente mesmo que o Checkmk já tenha voltado a OK.

    O job pode terminar alguns segundos depois da recuperação do serviço. Nesse
    intervalo o incidente sai do índice de abertos, mas a análise ainda precisa
    ser anexada ao histórico para preservar causa, evidências e conclusão.
    """
    rows = list_noc_incidents(limit=500, open_only=False, sync_jobs=False, settings=settings).get("items") or []
    return next((item for item in rows if str(item.get("job_id") or "") == job_id), None)


def _mark_job_paused(
    incident: dict[str, Any],
    reason: str,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Devolve o incidente ao estado observável sem tratar a trava como falha."""
    client = incident_store._redis(settings)
    incident = dict(incident)
    incident.update(
        {
            "status": "new",
            "job_id": None,
            "attention_reason": reason[:2000],
            "autonomy": {"eligible": False, "reason": reason, "paused": True},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    stored = incident_store._store(client, settings, incident)
    incident_store._append_event(
        client,
        settings,
        incident_id=str(stored.get("id") or ""),
        fingerprint=str(stored.get("fingerprint") or ""),
        event={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": "workflow",
            "source": "noc_autonomy_guard",
            "event_type": "investigation_paused",
            "reason": reason[:1000],
        },
    )
    return stored


def _safe_postprocess_result(
    job_result: dict[str, Any],
    result: dict[str, Any],
    *,
    settings: Settings,
    incident: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(job_result.get("metadata") or {})
    allowed, reason = job_runtime_authorization(metadata, settings=settings)
    recovered = str((incident or {}).get("status") or "") == "resolved"

    if allowed and not recovered:
        return result

    # A investigação pode terminar depois que o operador desliga a chave ou
    # depois que o próprio Checkmk já confirmou recuperação. Em ambos os casos
    # preservamos a análise/evidências, porém removemos a capacidade de executar
    # uma correção tardia. Sem approval_token, _auto_execute não inicia ações.
    if recovered:
        reason = "O Checkmk normalizou antes do término da investigação; causa e evidências foram preservadas sem executar correção tardia."
    safe = dict(result)
    safe["approval_token"] = None
    safe["runtime_autonomy"] = {"allowed": False, "reason": reason}
    if recovered:
        safe["recovered_before_investigation_completed"] = True
    return safe


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
        safe_result = _safe_postprocess_result(
            job_result,
            dict(job_result["result"]),
            settings=settings,
            incident=incident,
        )
        return postprocess_investigation_result(incident_id, safe_result, settings=settings)
    if status == "cancelled" and job_result.get("blocked_by_autonomy"):
        reason = str(job_result.get("autonomy_reason") or "Atuação autônoma pausada pelo operador.")
        return _mark_job_paused(incident, reason, settings=settings)
    if status in {"failed", "cancelled"}:
        reason = str(job_result.get("error") or f"job {status}")
        return mark_job_failure(incident_id, reason, settings=settings)
    return incident


def reconcile_noc_jobs(*, settings: Settings | None = None) -> dict[str, Any]:
    """Retoma o pós-processamento se o worker reiniciar entre job e supervisor.

    Inclui incidentes recentemente resolvidos para que uma investigação que
    terminou depois da recuperação ainda grave a causa no histórico.
    """
    settings = settings or get_settings()
    rows = list_noc_incidents(limit=500, open_only=False, sync_jobs=False, settings=settings).get("items") or []
    recovered = 0
    errors: list[dict[str, str]] = []
    for incident in rows:
        job_id = str(incident.get("job_id") or "")
        if not job_id:
            continue
        # A presença de autonomy indica que o supervisor já pós-processou o job.
        if "autonomy" in incident:
            continue
        try:
            job = get_job(job_id, settings=settings)
            if not job:
                continue
            status = str(job.get("status") or "")
            if status == "completed" and isinstance(job.get("result"), dict):
                safe_result = _safe_postprocess_result(
                    job,
                    dict(job["result"]),
                    settings=settings,
                    incident=incident,
                )
                postprocess_investigation_result(
                    str(incident.get("id") or ""),
                    safe_result,
                    settings=settings,
                )
                recovered += 1
            elif status == "cancelled" and job.get("blocked_by_autonomy"):
                _mark_job_paused(
                    incident,
                    str(job.get("autonomy_reason") or "Atuação autônoma pausada pelo operador."),
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
