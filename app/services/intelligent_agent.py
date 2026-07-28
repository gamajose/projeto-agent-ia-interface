from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Callable

from app.core.settings import Settings, get_settings
from app.services import dynamic_agent as engine
from app.services.ai_providers import (
    current_model_override,
    current_provider_override,
    get_provider,
    use_provider,
)
from app.services.persistence import update_investigation_analysis
from app.services.provider_preflight import preflight_provider
from app.services.provider_router import automatic_provider_order
from app.services.redaction import redact_object, redact_text


_INTELLIGENT_SESSION: ContextVar[bool] = ContextVar(
    "agent_intelligent_session",
    default=False,
)
_REASONING_TRACE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "agent_reasoning_trace",
    default=None,
)

_ORIGINAL_RENDER_STEPS: Callable[..., list[dict[str, Any]]] = engine.render_steps


MISSION_RULES = """
Você é o coordenador cognitivo de um agente AIOps. Responda somente JSON válido.
Transforme a descrição do operador em uma missão operacional verificável. Não presuma a causa.
Defina o que precisa ser comprovado, quais lacunas existem e quando a investigação pode terminar.
Não crie comandos e não escolha ferramentas nesta etapa.
Formato:
{
  "mission":"...",
  "success_criteria":["..."],
  "known_facts":["..."],
  "unknowns":["..."],
  "candidate_domains":["service|network|filesystem|memory|container|monitoring|system|other"],
  "constraints":["..."],
  "stop_conditions":["..."],
  "initial_confidence":0
}
""".strip()


CRITIC_RULES = """
Você é a IA crítica independente de uma investigação AIOps. Responda somente JSON válido.
Avalie se a conclusão está realmente sustentada pelas evidências executadas e pelos critérios de sucesso.
Não aceite afirmações baseadas apenas no histórico, no playbook ou em suposição da IA planejadora.
Diferencie ausência de evidência de evidência de ausência. Procure contradições e saltos lógicos.
Formato:
{
  "verdict":"accept|insufficient|contradictory",
  "evidence_coverage":0,
  "confidence":0,
  "supported_claims":["..."],
  "unsupported_claims":["..."],
  "contradictions":["..."],
  "missing_evidence":["..."],
  "safe_to_propose":false,
  "summary":"..."
}
""".strip()


_VALID_FINAL_STATUS = {"healthy", "attention", "critical", "inconclusive"}
_VALID_CRITIC_VERDICT = {"accept", "insufficient", "contradictory"}


def _integer(value: Any, *, minimum: int = 0, maximum: int = 100) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("campo numérico inválido") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"campo numérico fora do intervalo {minimum}-{maximum}")
    return number


def _list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} precisa ser uma lista")
    return value


def _validate_reasoning_output(purpose: str, result: dict[str, Any]) -> None:
    if not isinstance(result, dict):
        raise ValueError("a IA não retornou objeto JSON")

    if purpose == "mission_interpretation":
        if not str(result.get("mission") or "").strip():
            raise ValueError("missão ausente")
        _list(result.get("success_criteria"), "success_criteria")
        _list(result.get("unknowns"), "unknowns")
        _list(result.get("stop_conditions"), "stop_conditions")
        _integer(result.get("initial_confidence", 0))
        return

    if purpose.startswith("planning_round_"):
        _list(result.get("hypotheses"), "hypotheses")
        tools = result.get("tools") or result.get("commands") or []
        _list(tools, "tools")
        if not isinstance(result.get("done", False), bool):
            raise ValueError("done precisa ser booleano")
        _integer(result.get("confidence", 0))
        return

    if purpose.startswith("analysis_round_"):
        _list(result.get("findings"), "findings")
        _list(result.get("remaining_questions"), "remaining_questions")
        if not isinstance(result.get("needs_more_evidence", True), bool):
            raise ValueError("needs_more_evidence precisa ser booleano")
        _integer(result.get("confidence", 0))
        return

    if purpose == "final_analysis":
        status = str(result.get("status") or "")
        if status not in _VALID_FINAL_STATUS:
            raise ValueError("status final inválido")
        _integer(result.get("confidence", 0))
        if not str(result.get("summary") or "").strip():
            raise ValueError("resumo final ausente")
        _list(result.get("facts"), "facts")
        _list(result.get("evidence_map"), "evidence_map")
        return

    if purpose == "correction_planning":
        _list(result.get("actions"), "actions")
        return

    if purpose == "final_critic":
        verdict = str(result.get("verdict") or "")
        if verdict not in _VALID_CRITIC_VERDICT:
            raise ValueError("veredito crítico inválido")
        _integer(result.get("evidence_coverage", 0))
        _integer(result.get("confidence", 0))
        _list(result.get("unsupported_claims"), "unsupported_claims")
        _list(result.get("missing_evidence"), "missing_evidence")
        if not isinstance(result.get("safe_to_propose", False), bool):
            raise ValueError("safe_to_propose precisa ser booleano")


def _provider_candidates(
    provider_name: str | None,
    settings: Settings,
) -> list[tuple[str, str | None, str]]:
    selected = (
        provider_name
        or current_provider_override()
        or settings.ai_provider
        or "gemini"
    ).strip().lower()
    selected_model = current_model_override()
    candidates: list[tuple[str, str | None, str]] = [
        (selected, selected_model, "selected"),
    ]

    if not settings.agent_reasoning_provider_fallback:
        return candidates

    for name in automatic_provider_order(settings):
        if name == selected:
            continue
        candidates.append((name, None, "fallback"))
    return candidates[: max(1, settings.agent_reasoning_max_provider_attempts)]


def resilient_model_call(
    prompt: str,
    purpose: str,
    provider_name: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Executa uma etapa cognitiva com validação estrutural e failover de IA.

    O primeiro candidato é sempre o provedor selecionado para a investigação.
    Outros provedores só são consultados quando a chamada falha, retorna JSON
    inválido ou não satisfaz o contrato da etapa.
    """
    settings = get_settings()
    diagnostics: dict[str, Any] = {
        "purpose": purpose,
        "attempts": [],
        "success": False,
    }

    for candidate, requested_model, source in _provider_candidates(provider_name, settings):
        model = requested_model
        try:
            if source == "fallback":
                preflight = preflight_provider(candidate, settings, quick=False)
                if not preflight.selectable:
                    diagnostics["attempts"].append(
                        {
                            "provider": candidate,
                            "model": preflight.model,
                            "source": source,
                            "status": "skipped",
                            "error": preflight.detail,
                        }
                    )
                    continue
                model = preflight.model or None

            with use_provider(candidate, model):
                provider = get_provider(candidate, settings, model)
                attempt: dict[str, Any] = {
                    "provider": provider.name,
                    "model": provider.model,
                    "source": source,
                }
                result, metadata = provider.generate_json(redact_text(prompt))
                attempt.update(metadata)
                _validate_reasoning_output(purpose, result)
                attempt["status"] = "success"
                diagnostics["attempts"].append(attempt)
                diagnostics.update(
                    {
                        "success": True,
                        "provider": provider.name,
                        "model": provider.model,
                        "failover_used": source == "fallback",
                    }
                )
                result["_ai_model"] = provider.model
                result["_ai_provider"] = provider.name
                trace = _REASONING_TRACE.get()
                if trace is not None:
                    trace.append(redact_object(diagnostics))
                return result, diagnostics
        except Exception as exc:
            diagnostics["attempts"].append(
                {
                    "provider": candidate,
                    "model": model or "",
                    "source": source,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    errors = [
        f"{item.get('provider')}: {item.get('error')}"
        for item in diagnostics["attempts"]
        if item.get("error")
    ]
    diagnostics["error"] = " | ".join(errors[-3:]) or "nenhum provedor respondeu"
    trace = _REASONING_TRACE.get()
    if trace is not None:
        trace.append(redact_object(diagnostics))
    return None, diagnostics


def _render_steps_advisory(playbook: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    if (
        _INTELLIGENT_SESSION.get()
        and settings.agent_playbook_advisory_only
    ):
        return []
    return _ORIGINAL_RENDER_STEPS(playbook, context)


def _fallback_mission(objective: str) -> dict[str, Any]:
    return {
        "mission": objective,
        "success_criteria": [
            "coletar evidências atuais do alvo",
            "confirmar ou descartar a causa provável",
            "produzir conclusão e proposta sem executar alterações",
        ],
        "known_facts": [f"Descrição recebida: {objective}"],
        "unknowns": ["estado atual do alvo", "causa raiz", "impacto operacional"],
        "candidate_domains": ["other"],
        "constraints": ["somente leitura durante a investigação"],
        "stop_conditions": ["causa sustentada por evidência ou lacuna explicitamente identificada"],
        "initial_confidence": 0,
        "fallback": True,
    }


def interpret_mission(objective: str) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = (
        MISSION_RULES
        + "\n\nDESCRIÇÃO DO OPERADOR:\n"
        + redact_text(objective.strip() or "validar a saúde geral do servidor")
    )
    mission, diagnostics = resilient_model_call(prompt, "mission_interpretation")
    return mission or _fallback_mission(objective), diagnostics


def _enriched_objective(objective: str, mission: dict[str, Any]) -> str:
    original = objective.strip() or "validar a saúde geral do servidor"
    safe_mission = {
        key: value
        for key, value in mission.items()
        if not str(key).startswith("_ai_")
    }
    return (
        f"OBJETIVO ORIGINAL: {original}\n\n"
        "MISSÃO E CRITÉRIOS DEFINIDOS PELA IA COORDENADORA:\n"
        + json.dumps(redact_object(safe_mission), ensure_ascii=False, default=str)
    )


def _compact_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in evidence[-20:]:
        compact.append(
            {
                "tool": item.get("tool"),
                "command": item.get("command"),
                "purpose": item.get("purpose"),
                "status": item.get("status"),
                "exit_code": item.get("exit_code"),
                "stdout_excerpt": str(item.get("stdout") or "")[-1800:],
                "stderr_excerpt": str(item.get("stderr") or "")[-600:],
                "normalized": item.get("normalized") or {},
            }
        )
    return compact


def critique_result(
    *,
    mission: dict[str, Any],
    result: dict[str, Any],
    settings: Settings,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    payload = {
        "mission": mission,
        "environment": result.get("environment_classification") or {},
        "analysis": result.get("analysis") or {},
        "round_assessments": result.get("round_assessments") or [],
        "deterministic_signals": result.get("deterministic_signals") or [],
        "evidence": _compact_evidence(list(result.get("evidence") or [])),
    }
    prompt = (
        CRITIC_RULES
        + "\n\nINVESTIGAÇÃO A SER AUDITADA:\n"
        + json.dumps(redact_object(payload), ensure_ascii=False, default=str)
    )
    return resilient_model_call(
        prompt,
        "final_critic",
        provider_name=settings.ai_reviewer_provider,
    )


def _apply_critic(
    result: dict[str, Any],
    critic: dict[str, Any] | None,
    settings: Settings,
) -> None:
    analysis = dict(result.get("analysis") or {})
    if not critic:
        analysis["critic"] = {
            "verdict": "unavailable",
            "summary": "A IA crítica não respondeu; nenhuma aprovação corretiva foi liberada.",
            "safe_to_propose": False,
        }
        result["approval_token"] = None
        analysis.pop("approval", None)
        result["analysis"] = analysis
        return

    public_critic = {
        key: value
        for key, value in critic.items()
        if not str(key).startswith("_ai_")
    }
    analysis["critic"] = public_critic
    coverage = int(critic.get("evidence_coverage") or 0)
    verdict = str(critic.get("verdict") or "insufficient")
    accepted = (
        verdict == "accept"
        and coverage >= settings.agent_critic_min_coverage
        and bool(critic.get("safe_to_propose"))
    )

    if accepted:
        analysis["confidence"] = min(
            int(analysis.get("confidence") or 0),
            int(critic.get("confidence") or 0),
        )
        result["analysis"] = analysis
        return

    missing = [str(item) for item in critic.get("missing_evidence") or [] if str(item).strip()]
    unsupported = [
        str(item)
        for item in critic.get("unsupported_claims") or []
        if str(item).strip()
    ]
    reasons = [*unsupported, *missing]
    analysis["status"] = "inconclusive"
    analysis["confidence"] = min(
        int(analysis.get("confidence") or 0),
        int(critic.get("confidence") or 0),
        coverage,
    )
    analysis["conclusion"] = (
        "A conclusão inicial não passou pela validação da IA crítica. "
        + (str(critic.get("summary") or "Faltou cobertura de evidências."))
    )
    recommendations = list(analysis.get("recommendations") or [])
    if reasons:
        recommendations.append("Coletar evidências adicionais: " + "; ".join(reasons[:8]))
    analysis["recommendations"] = list(dict.fromkeys(recommendations))

    for item in analysis.get("proposed_actions") or []:
        if isinstance(item, dict) and item.get("status") == "proposed":
            item["status"] = "critic_rejected"
            item["reason"] = "a conclusão não atingiu cobertura mínima de evidências"

    result["approval_token"] = None
    analysis.pop("approval", None)
    result["analysis"] = analysis
    result["review"] = {
        **dict(result.get("review") or {}),
        "approved": False,
        "reason": "IA crítica rejeitou a cobertura de evidências da conclusão.",
    }


def run_dynamic_investigation(**kwargs: Any) -> dict[str, Any]:
    """Executa o mesmo motor operacional com coordenação e crítica de IA.

    O wrapper não cria um segundo executor. Ele acrescenta missão estruturada,
    contratos de saída, failover cognitivo, playbook consultivo e crítica final
    ao loop já existente de planejamento, ferramentas, reflexão e replanejamento.
    """
    settings = get_settings()
    if not settings.agent_intelligent_reasoning_enabled:
        return engine.run_dynamic_investigation(**kwargs)

    original_context = str(kwargs.get("context") or "").strip()
    trace: list[dict[str, Any]] = []
    session_token = _INTELLIGENT_SESSION.set(True)
    trace_token = _REASONING_TRACE.set(trace)
    try:
        mission, mission_diagnostics = interpret_mission(original_context)
        intelligent_kwargs = {
            **kwargs,
            "context": _enriched_objective(original_context, mission),
        }
        result = engine.run_dynamic_investigation(**intelligent_kwargs)
        critic: dict[str, Any] | None = None
        critic_diagnostics: dict[str, Any] = {
            "purpose": "final_critic",
            "success": False,
            "status": "disabled",
        }
        if settings.agent_critic_enabled:
            critic, critic_diagnostics = critique_result(
                mission=mission,
                result=result,
                settings=settings,
            )
            _apply_critic(result, critic, settings)

        result["context"] = original_context or "validar a saúde geral do servidor"
        result["intelligence"] = {
            "enabled": True,
            "loop": "understand-plan-act-observe-reflect-replan-critic",
            "mission": redact_object(mission),
            "playbook_role": (
                "advisory"
                if settings.agent_playbook_advisory_only
                else "initial_steps"
            ),
            "provider_failover": settings.agent_reasoning_provider_fallback,
            "critic": redact_object(critic or {}),
            "reasoning_trace": redact_object(trace),
        }
        diagnostics = list(result.get("ai_diagnostics") or [])
        result["ai_diagnostics"] = [
            mission_diagnostics,
            *diagnostics,
            critic_diagnostics,
        ]
        analysis = dict(result.get("analysis") or {})
        analysis["ai_diagnostics"] = result["ai_diagnostics"]
        result["analysis"] = analysis

        investigation_id = result.get("investigation_id")
        if investigation_id:
            update_investigation_analysis(
                str(investigation_id),
                redact_object(analysis),
            )
        return result
    finally:
        _REASONING_TRACE.reset(trace_token)
        _INTELLIGENT_SESSION.reset(session_token)


# O motor existente chama estes símbolos do próprio módulo. A substituição é
# única no carregamento e usa ContextVars, portanto continua segura entre jobs.
engine._model_call = resilient_model_call
engine.render_steps = _render_steps_advisory
