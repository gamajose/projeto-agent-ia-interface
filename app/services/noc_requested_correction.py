from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings, get_settings
from app.services.approved_execution import execute_approved_investigation
from app.services.jobs import get_job
from app.services.noc_action_policy import policy_allows_autonomous_correction
from app.services.noc_autonomy_control import get_noc_autonomy_control, scope_matches_problem
from app.services.noc_checkmk_runtime import is_green, query_incident_service
from app.services.noc_deterministic_skill import (
    is_ai_dependency_failure,
    run_deterministic_skill_correction,
)
from app.services import noc_incidents as incident_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _incident_update(incident_id: str, changes: dict[str, Any], settings: Settings) -> dict[str, Any] | None:
    client = incident_store._redis(settings)
    incident = incident_store._load(client, settings, incident_id)
    if not incident:
        return None
    incident.update(changes)
    return incident_store._store(client, settings, incident)


def _event(incident: dict[str, Any], event_type: str, data: dict[str, Any], settings: Settings) -> None:
    incident_store._append_event(
        incident_store._redis(settings),
        settings,
        incident_id=str(incident.get("id") or ""),
        fingerprint=str(incident.get("fingerprint") or ""),
        event={
            "timestamp": _now().isoformat(),
            "kind": "workflow",
            "source": "noc_requested_correction",
            "event_type": event_type,
            **data,
        },
    )


def _store_job_phase(
    job_id: str,
    *,
    status: str,
    percent: int,
    stage: str,
    detail: str,
    settings: Settings,
    extra: dict[str, Any] | None = None,
) -> None:
    if not job_id:
        return
    current = get_job(job_id, settings=settings) or {"job_id": job_id}
    now = _now().isoformat()
    payload = {
        **current,
        "job_id": job_id,
        "status": status,
        "percent": max(0, min(100, int(percent))),
        "updated_at": now,
        "current_phase": {
            "stage": stage,
            "status": "running" if status == "running" else status,
            "detail": detail,
            "percent": max(0, min(100, int(percent))),
            "updated_at": now,
        },
        **dict(extra or {}),
    }
    incident_store._redis(settings).setex(
        f"{settings.agent_result_prefix}{job_id}",
        max(60, int(settings.agent_job_ttl_seconds)),
        __import__("json").dumps(payload, ensure_ascii=False, default=str),
    )


def _environment(result: dict[str, Any], incident: dict[str, Any]) -> EnvironmentType:
    classification = dict(result.get("environment_classification") or {})
    raw = classification.get("environment") or incident.get("environment") or EnvironmentType.UNKNOWN.value
    if hasattr(raw, "value"):
        raw = raw.value
    try:
        return EnvironmentType(str(raw or EnvironmentType.UNKNOWN.value).strip().casefold())
    except ValueError:
        return EnvironmentType.UNKNOWN


def _correction_intent(incident: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    if bool(incident.get("manual_correction_requested")):
        return True, "manual_selected"

    control = get_noc_autonomy_control(settings=settings)
    if not bool(control.get("enabled")):
        return False, "autonomia desligada"
    problem = {
        "site_id": incident.get("site") or incident.get("site_id"),
        "host": incident.get("host"),
        "problem_key": incident.get("problem_key"),
    }
    if not scope_matches_problem(problem, control):
        return False, "incidente fora do escopo autônomo atual"
    return True, "automatic"


def _allowed_tools(
    *,
    source: str,
    analysis: dict[str, Any],
    settings: Settings,
) -> set[str]:
    global_tools = {
        item.strip()
        for item in str(settings.noc_self_heal_tools or "").split(",")
        if item.strip()
    }
    playbook_tools = {
        str(item).strip()
        for item in (analysis.get("recovery_scope") or {}).get("allowed_correction_tools") or []
        if str(item).strip()
    }
    if source == "manual_selected":
        # O clique em "Arrumar selecionados" autoriza somente as correções que
        # pertencem ao playbook/skill da investigação ou à allowlist NOC global.
        return playbook_tools | global_tools
    # Automático continua mais conservador: somente allowlist global e, quando
    # o playbook declarou escopo, a ação também precisa pertencer a ele.
    return global_tools if not playbook_tools else global_tools & playbook_tools


def _eligibility(
    incident: dict[str, Any],
    result: dict[str, Any],
    *,
    source: str,
    settings: Settings,
) -> tuple[bool, str, set[str]]:
    category_allowed, _category, category_reason = policy_allows_autonomous_correction(incident)
    if not category_allowed:
        return False, category_reason, set()

    environment = _environment(result, incident)
    if not environment_allows_correction(environment):
        return False, f"ambiente {environment.value} não permite correção", set()

    classification = dict(result.get("environment_classification") or {})
    if source == "automatic":
        try:
            confidence = int(classification.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0
        if confidence < 90:
            return False, "classificação do ambiente abaixo de 90% para correção automática", set()

    analysis = dict(result.get("analysis") or {})
    try:
        confidence = int(analysis.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < int(settings.noc_self_heal_min_confidence):
        return False, "confiança da análise abaixo do mínimo para correção", set()

    review = dict(result.get("review") or analysis.get("review") or {})
    if not review.get("approved"):
        return False, "segunda IA não aprovou a correção", set()
    if not result.get("approval_token"):
        return False, "a investigação não gerou token para as ações aprovadas", set()

    proposals = [
        item
        for item in analysis.get("proposed_actions") or []
        if isinstance(item, dict) and item.get("status") == "proposed"
    ]
    if not proposals:
        return False, "nenhuma ação corretiva foi proposta", set()

    requested = {
        str(item.get("tool") or "").strip()
        for item in proposals
        if str(item.get("tool") or "").strip()
    }
    allowed = _allowed_tools(source=source, analysis=analysis, settings=settings)
    if not requested or not requested.issubset(allowed):
        return False, "a correção proposta não pertence ao escopo seguro autorizado", allowed
    return True, "correção segura autorizada", allowed


def _resolved(
    incident: dict[str, Any],
    runtime: dict[str, Any],
    *,
    execution: dict[str, Any],
    source: str,
    settings: Settings,
) -> dict[str, Any]:
    now = _now().isoformat()
    updated = _incident_update(
        str(incident.get("id") or ""),
        {
            "status": "resolved",
            "severity": "healthy",
            "current_state": "OK",
            "state_kind": "ok",
            "resolved_at": now,
            "resolution_source": f"{source}_correction",
            "last_checkmk_runtime": runtime,
            "attention_reason": None,
            "autopilot_execution": execution,
            "correction_intent_source": source,
        },
        settings,
    ) or incident
    client = incident_store._redis(settings)
    client.srem(incident_store._open_key(settings), str(updated.get("id") or ""))
    fingerprint_key = incident_store._fingerprint_key(settings, str(updated.get("fingerprint") or ""))
    if client.get(fingerprint_key) == str(updated.get("id") or ""):
        client.delete(fingerprint_key)
    _event(updated, "correction_resolved", {"source": source, "runtime": runtime}, settings)
    return updated


def attempt_requested_correction(
    incident_id: str,
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Executa o ciclo corrigir -> revalidar quando o NOC recebeu intenção de correção.

    A investigação por IA continua sendo o caminho preferencial. Quando o
    bloqueio é exclusivamente a indisponibilidade/baixa confiança da IA e o
    incidente corresponde a uma Skill determinística já validada em campo, o
    NOC pode executar a ferramenta estruturada dessa Skill sem depender do
    Ensemble. Políticas, allowlist, rota do cliente e pré-condições funcionais
    continuam obrigatórias.
    """

    settings = settings or get_settings()
    client = incident_store._redis(settings)
    incident = incident_store._load(client, settings, incident_id)
    if not incident:
        return None
    if str(incident.get("status") or "") == "resolved":
        return incident
    if incident.get("autopilot_execution"):
        return incident

    has_intent, source = _correction_intent(incident, settings)
    if not has_intent:
        return incident

    job_id = str(incident.get("job_id") or "")
    eligible, reason, allowed_tools = _eligibility(
        incident,
        result,
        source=source,
        settings=settings,
    )

    deterministic_execution: dict[str, Any] | None = None
    if not eligible and is_ai_dependency_failure(reason):
        _store_job_phase(
            job_id,
            status="running",
            percent=88,
            stage="deterministic_skill",
            detail="IA indisponível ou inconclusiva. Validando a Skill conhecida diretamente no ambiente.",
            settings=settings,
            extra={"resolution_status": "correcting"},
        )
        incident = _incident_update(
            incident_id,
            {
                "status": "correcting",
                "attention_reason": None,
                "correction_intent_source": source,
                "correction_fallback": "deterministic_skill",
            },
            settings,
        ) or incident
        _event(incident, "deterministic_skill_started", {"source": source, "ai_blocker": reason}, settings)
        deterministic_execution = run_deterministic_skill_correction(
            incident,
            result,
            settings=settings,
        )
        if deterministic_execution and str(deterministic_execution.get("status") or "") == "validated":
            eligible = True
            reason = (
                "Skill determinística validou a correção sem depender do provedor de IA: "
                f"{deterministic_execution.get('deterministic_skill') or 'skill conhecida'}"
            )
            allowed_tools = {
                str(item.get("tool") or "").strip()
                for item in deterministic_execution.get("results") or []
                if isinstance(item, dict) and str(item.get("tool") or "").strip()
            }
            _event(
                incident,
                "deterministic_skill_validated",
                {
                    "source": source,
                    "skill": deterministic_execution.get("deterministic_skill"),
                    "ai_blocker": reason,
                },
                settings,
            )
        elif deterministic_execution:
            fallback_reason = str(
                deterministic_execution.get("reason")
                or "pré-condições da Skill determinística não foram confirmadas"
            )
            reason = f"{reason}. Skill determinística não executada/validada: {fallback_reason}"

    if not eligible:
        updated = _incident_update(
            incident_id,
            {
                "status": "needs_attention",
                "attention_reason": f"Correção solicitada, mas não pôde ser executada automaticamente: {reason}",
                "correction_intent_source": source,
                "correction_eligibility": {"eligible": False, "reason": reason},
                **(
                    {"deterministic_execution": deterministic_execution}
                    if deterministic_execution is not None
                    else {}
                ),
            },
            settings,
        ) or incident
        _store_job_phase(
            job_id,
            status="failed",
            percent=100,
            stage="correction_blocked",
            detail=str(updated.get("attention_reason") or reason),
            settings=settings,
            extra={"resolution_status": "needs_attention"},
        )
        _event(updated, "correction_blocked", {"source": source, "reason": reason}, settings)
        return updated

    if deterministic_execution is None:
        _store_job_phase(
            job_id,
            status="running",
            percent=88,
            stage="correction",
            detail="Diagnóstico concluído. Executando a correção segura aprovada.",
            settings=settings,
            extra={"resolution_status": "correcting"},
        )
        incident = _incident_update(
            incident_id,
            {
                "status": "correcting",
                "attention_reason": None,
                "correction_intent_source": source,
                "correction_eligibility": {"eligible": True, "reason": reason},
            },
            settings,
        ) or incident
        _event(incident, "correction_started", {"source": source}, settings)
    else:
        incident = _incident_update(
            incident_id,
            {
                "status": "correcting",
                "attention_reason": None,
                "correction_intent_source": source,
                "correction_eligibility": {"eligible": True, "reason": reason},
                "deterministic_execution": deterministic_execution,
            },
            settings,
        ) or incident

    try:
        execution: dict[str, Any] | None = deterministic_execution
        if execution is None:
            current_token = str(result.get("approval_token") or "")
            investigation_id = str(result.get("investigation_id") or "")
            max_rounds = max(1, int(settings.noc_autonomy_max_approval_rounds))
            for _round in range(1, max_rounds + 1):
                execution = execute_approved_investigation(
                    investigation_id,
                    current_token,
                    requested_by=(
                        "Operador NOC - Arrumar selecionados"
                        if source == "manual_selected"
                        else "NOC Autônomo - categoria autorizada"
                    ),
                    settings=settings,
                )
                if not execution.get("new_approval_required"):
                    break
                pending = [item for item in execution.get("pending_actions") or [] if isinstance(item, dict)]
                pending_tools = {
                    str(item.get("tool") or "").strip()
                    for item in pending
                    if str(item.get("tool") or "").strip()
                }
                review = dict(execution.get("pending_review") or {})
                next_token = str(execution.get("next_approval_token") or "")
                if not pending_tools or not pending_tools.issubset(allowed_tools) or not review.get("approved") or not next_token:
                    break
                current_token = next_token

        execution = execution or {"status": "failed", "state": "no_execution"}
        if str(execution.get("status") or "") != "validated":
            updated = _incident_update(
                incident_id,
                {
                    "status": "needs_attention",
                    "attention_reason": "A correção foi executada, mas a validação local não confirmou recuperação.",
                    "autopilot_execution": execution,
                    "correction_intent_source": source,
                },
                settings,
            ) or incident
            _store_job_phase(
                job_id,
                status="failed",
                percent=100,
                stage="correction_failed",
                detail=str(updated.get("attention_reason") or "Correção não validada."),
                settings=settings,
                extra={"resolution_status": "needs_attention"},
            )
            _event(updated, "correction_failed", {"source": source, "execution": execution}, settings)
            return updated

        _store_job_phase(
            job_id,
            status="running",
            percent=96,
            stage="checkmk_revalidation",
            detail="Correção executada. Revalidando o sensor no Checkmk.",
            settings=settings,
            extra={"resolution_status": "validating"},
        )
        incident = _incident_update(
            incident_id,
            {
                "status": "watching",
                "autopilot_execution": execution,
                "watch_started_at": _now().isoformat(),
                "next_check_at": (_now() + timedelta(seconds=int(settings.noc_watch_interval_seconds))).isoformat(),
                "attention_reason": None,
                "correction_intent_source": source,
            },
            settings,
        ) or incident
        runtime = query_incident_service(incident, force=True, settings=settings)
        incident = _incident_update(
            incident_id,
            {
                "last_checkmk_runtime": runtime,
                "recheck_count": int(incident.get("recheck_count") or 0) + 1,
            },
            settings,
        ) or incident
        if is_green(runtime):
            resolved = _resolved(incident, runtime, execution=execution, source=source, settings=settings)
            _store_job_phase(
                job_id,
                status="completed",
                percent=100,
                stage="resolved",
                detail="Correção aplicada e Checkmk confirmou o sensor em OK.",
                settings=settings,
                extra={"resolution_status": "resolved"},
            )
            return resolved

        _store_job_phase(
            job_id,
            status="running",
            percent=96,
            stage="checkmk_watch",
            detail="Correção aplicada. Aguardando o Checkmk confirmar o estado OK.",
            settings=settings,
            extra={"resolution_status": "watching"},
        )
        return incident
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        updated = _incident_update(
            incident_id,
            {
                "status": "needs_attention",
                "attention_reason": f"Falha ao executar a correção solicitada: {reason}"[:2000],
                "correction_intent_source": source,
            },
            settings,
        ) or incident
        _store_job_phase(
            job_id,
            status="failed",
            percent=100,
            stage="correction_error",
            detail=str(updated.get("attention_reason") or reason),
            settings=settings,
            extra={"resolution_status": "needs_attention"},
        )
        _event(updated, "correction_error", {"source": source, "error": reason[:1000]}, settings)
        return updated
