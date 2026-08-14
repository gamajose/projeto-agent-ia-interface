from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services import jobs
from app.services.noc_autonomy_control import authorize_noc_job, get_noc_autonomy_control, scope_matches_problem


_INSTALLED = False
_STALE_CONTROL_REASON = "escopo autônomo mudou depois que o job entrou na fila"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_noc_job(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("source") or "") in {"checkmk_master", "noc_reinvestigation"}


def _problem_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "site_id": metadata.get("site_id") or (metadata.get("noc_routing") or {}).get("site_id"),
        "host": metadata.get("checkmk_host") or metadata.get("host"),
        "problem_key": metadata.get("checkmk_problem_key") or metadata.get("problem_key"),
    }


def _reinvestigation_authorized(metadata: dict[str, Any], settings: Any) -> tuple[bool, str]:
    control = get_noc_autonomy_control(settings=settings)
    if not control.get("enabled"):
        return False, "atuação autônoma foi desligada pelo operador"
    problem = _problem_from_metadata(metadata)
    if str(control.get("mode") or "automatic") == "automatic":
        return True, "reinvestigação autorizada pelo modo automático"
    if scope_matches_problem(problem, control):
        return True, "reinvestigação pertence ao escopo selecionado"
    return False, "reinvestigação ficou fora do escopo atual"


def _reauthorize_stale_job(metadata: dict[str, Any], settings: Any) -> tuple[bool, str]:
    """Revalida um job antigo sem furar a política atual do operador.

    A revisão do controle muda sempre que o operador altera o modo/escopo. Um
    job que já estava na fila não deve ficar pausado apenas por carregar uma
    revisão antiga: ele pode seguir se o controle atual continua ligado e o
    problema ainda pertence ao escopo atual. Desligar os agentes ou retirar o
    problema do escopo continua bloqueando a execução antes do SSH.
    """
    control = get_noc_autonomy_control(settings=settings)
    if not control.get("enabled"):
        return False, "atuação autônoma foi desligada pelo operador"
    problem = _problem_from_metadata(metadata)
    if not scope_matches_problem(problem, control):
        return False, "job não pertence mais ao escopo autônomo atual"
    if str(control.get("mode") or "automatic") == "automatic":
        return True, "job da fila revalidado pelo modo automático atual"
    return True, "job da fila revalidado pelo escopo selecionado atual"


def job_runtime_authorization(metadata: dict[str, Any], *, settings: Any) -> tuple[bool, str]:
    if not _is_noc_job(metadata):
        return True, "job fora do fluxo NOC"
    if str(metadata.get("source") or "") == "noc_reinvestigation" and not metadata.get("noc_run_id"):
        return _reinvestigation_authorized(metadata, settings)

    allowed, reason = authorize_noc_job(metadata, settings=settings)
    if allowed:
        return True, reason
    if reason == _STALE_CONTROL_REASON:
        return _reauthorize_stale_job(metadata, settings)
    return False, reason


def install_noc_job_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = jobs._execute_job
    if getattr(original, "_noc_runtime_guard", False):
        _INSTALLED = True
        return

    def guarded(job: dict[str, Any], *, settings: Any) -> dict[str, Any]:
        metadata = dict(job.get("metadata") or {})
        allowed, reason = job_runtime_authorization(metadata, settings=settings)
        if _is_noc_job(metadata) and not allowed:
            job_id = str(job.get("job_id") or "")
            now = _now()
            current = jobs.get_job(job_id, settings=settings) or {}
            payload = {
                **current,
                "job_id": job_id,
                "status": "cancelled",
                "blocked_by_autonomy": True,
                "autonomy_reason": reason,
                "metadata": metadata,
                "cancelled_at": now,
                "completed_at": now,
                "updated_at": now,
                "percent": int(current.get("percent") or 0),
                "error": None,
                "current_phase": {
                    "stage": "autonomy_guard",
                    "status": "cancelled",
                    "detail": f"Job NOC não abriu SSH: {reason}.",
                    "percent": int(current.get("percent") or 0),
                    "updated_at": now,
                },
            }
            client = jobs._redis(settings)
            jobs._store(client, settings, job_id, payload)
            return payload

        result = original(job, settings=settings)
        if isinstance(result, dict):
            result = {**result, "metadata": metadata}
            job_id = str(result.get("job_id") or job.get("job_id") or "")
            if job_id:
                jobs._store(jobs._redis(settings), settings, job_id, result)
        return result

    guarded._noc_runtime_guard = True  # type: ignore[attr-defined]
    jobs._execute_job = guarded
    _INSTALLED = True
