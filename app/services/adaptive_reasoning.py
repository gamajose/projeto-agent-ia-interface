from __future__ import annotations

import json
from typing import Any, Callable

from app.services import dynamic_agent as engine
from app.services import intelligent_agent, operational_memory, persistence
from app.services.adaptive_hypotheses import (
    build_adaptive_hypothesis_state,
    enrich_analysis_with_hypotheses,
)
from app.services.adaptive_incident_graph import (
    build_adaptive_dependency_graph,
    group_related_alerts,
    memory_guidance,
)
from app.services.environment_fingerprint import build_environment_fingerprint


_INSTALLED = False
_ORIGINAL_MODEL_CALL: Callable[..., tuple[dict[str, Any] | None, dict[str, Any]]] | None = None
_ORIGINAL_RUN: Callable[..., dict[str, Any]] | None = None
_ORIGINAL_BUILD_MEMORY: Callable[..., dict[str, Any]] | None = None


def _payload_from_prompt(prompt: str) -> dict[str, Any]:
    markers = ("\n\nENTRADA:\n", "\n\nDADOS:\n", "\n\nANÁLISE E EVIDÊNCIAS:\n")
    for marker in markers:
        if marker not in prompt:
            continue
        raw = prompt.rsplit(marker, 1)[-1].strip()
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _compact_hypothesis_context(state: dict[str, Any]) -> dict[str, Any]:
    hypotheses: list[dict[str, Any]] = []
    for item in state.get("hypotheses") or []:
        if not isinstance(item, dict):
            continue
        hypotheses.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "mechanism": item.get("mechanism"),
                "status": item.get("status"),
                "band": item.get("band"),
                "supporting_evidence": [
                    {
                        "command": evidence.get("command"),
                        "excerpt": evidence.get("excerpt"),
                    }
                    for evidence in item.get("supporting_evidence") or []
                    if isinstance(evidence, dict)
                ][:4],
                "contradicting_evidence": [
                    {
                        "command": evidence.get("command"),
                        "excerpt": evidence.get("excerpt"),
                    }
                    for evidence in item.get("contradicting_evidence") or []
                    if isinstance(evidence, dict)
                ][:3],
                "missing_tests": list(item.get("missing_tests") or [])[:3],
            }
        )
    return {
        "symptom": state.get("symptom"),
        "hypotheses": hypotheses[:8],
        "leader": state.get("leader"),
        "confirmed_cause": state.get("confirmed_cause"),
        "next_best_tests": list(state.get("next_best_tests") or [])[:6],
        "stop_decision": state.get("stop_decision"),
        "novelty": state.get("novelty"),
    }


def enrich_adaptive_prompt(prompt: str, purpose: str) -> str:
    if not (
        purpose.startswith("planning_round_")
        or purpose.startswith("analysis_round_")
        or purpose in {"final_analysis", "final_critic", "correction_planning"}
    ):
        return prompt
    payload = _payload_from_prompt(prompt)
    if not payload:
        return prompt

    objective = str(payload.get("objective") or payload.get("context") or "")
    analysis = dict(payload.get("analysis") or {})
    state = build_adaptive_hypothesis_state(
        objective=objective,
        profile=payload.get("profile"),
        evidence=list(payload.get("evidence") or payload.get("round_evidence") or []),
        assessments=list(payload.get("round_assessments") or payload.get("previous_assessments") or []),
        previous_state=analysis.get("adaptive_hypotheses"),
        runtime_context=dict(payload.get("runtime_context") or {}),
        similar_history=list(payload.get("similar_history") or payload.get("history") or []),
    )
    compact = _compact_hypothesis_context(state)
    guidance = """
MOTOR ADAPTATIVO DE HIPÓTESES
- O alerta informado é o sintoma inicial e não deve ser usado como causa raiz.
- Atualize a investigação conforme a árvore abaixo. Confirme, descarte ou mantenha em teste cada mecanismo com base somente nas evidências executadas.
- Priorize os próximos testes indicados quando forem compatíveis com o catálogo real do host.
- Não repita ferramenta já executada sem uma justificativa nova.
- Uma hipótese marcada como confirmada possui evidência direta e não contraditória. Nesse caso, não apresente percentuais concorrentes ao operador; apresente a causa confirmada e liste alternativas apenas como descartadas nos detalhes técnicos.
- Se a hipótese líder ainda estiver em teste, selecione a ferramenta que melhor separa as causas concorrentes.
- Histórico e memória apenas priorizam caminhos; nunca substituem evidência atual.
""".strip()
    return (
        guidance
        + "\n\nESTADO ADAPTATIVO ATUAL:\n"
        + json.dumps(compact, ensure_ascii=False, default=str)
        + "\n\n"
        + prompt
    )


def _adaptive_model_call(
    prompt: str,
    purpose: str,
    provider_name: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    assert _ORIGINAL_MODEL_CALL is not None
    return _ORIGINAL_MODEL_CALL(
        enrich_adaptive_prompt(prompt, purpose),
        purpose,
        provider_name,
    )


def _playbook_id(result: dict[str, Any]) -> str | None:
    playbook = result.get("playbook") or {}
    if isinstance(playbook, dict) and playbook.get("id"):
        return str(playbook["id"])
    for plan in result.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        item = plan.get("playbook") or {}
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
    return None


def _enrich_result(result: dict[str, Any]) -> dict[str, Any]:
    fingerprint = build_environment_fingerprint(
        identity=dict(result.get("identity") or {}),
        runtime_context=dict(result.get("runtime_context") or {}),
        evidence=list(result.get("evidence") or []),
        profile=result.get("profile"),
        environment=dict(result.get("environment_classification") or {}),
    )
    result["environment_fingerprint"] = fingerprint
    enrich_analysis_with_hypotheses(result)

    analysis = dict(result.get("analysis") or {})
    adaptive = dict(analysis.get("adaptive_hypotheses") or result.get("adaptive_hypotheses") or {})
    existing_incident = dict(analysis.get("incident_intelligence") or {})
    existing_correlation = dict(existing_incident.get("alert_correlation") or {})
    grouped = group_related_alerts(
        objective=str(result.get("context") or ""),
        adaptive_state=adaptive,
        existing_correlation=existing_correlation,
    )
    graph = build_adaptive_dependency_graph(
        fingerprint=fingerprint,
        adaptive_state=adaptive,
        objective=str(result.get("context") or ""),
    )
    memory = memory_guidance(list(result.get("similar_history") or []))

    analysis["environment_fingerprint"] = fingerprint
    analysis["adaptive_alert_grouping"] = grouped
    analysis["adaptive_dependency_graph"] = graph
    analysis["validated_memory_guidance"] = memory
    analysis["analysis_mode"] = "adaptive_dynamic"
    analysis["operational_memory"] = _adaptive_memory(
        objective=str(result.get("context") or ""),
        profile=result.get("profile"),
        playbook_id=_playbook_id(result),
        analysis=analysis,
        evidence=list(result.get("evidence") or []),
        corrections=list(result.get("corrections") or []),
        target=result.get("target"),
        hostname=result.get("hostname"),
    )
    result["adaptive_alert_grouping"] = grouped
    result["adaptive_dependency_graph"] = graph
    result["validated_memory_guidance"] = memory
    result["analysis"] = analysis
    return result


def _adaptive_run(**kwargs: Any) -> dict[str, Any]:
    assert _ORIGINAL_RUN is not None
    result = _ORIGINAL_RUN(**kwargs)
    return _enrich_result(result)


def _adaptive_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    assert _ORIGINAL_BUILD_MEMORY is not None
    memory = dict(_ORIGINAL_BUILD_MEMORY(*args, **kwargs) or {})
    analysis = dict(kwargs.get("analysis") or {})
    fingerprint = dict(analysis.get("environment_fingerprint") or {})
    adaptive = dict(analysis.get("adaptive_hypotheses") or {})
    leader = dict(adaptive.get("confirmed_cause") or adaptive.get("leader") or {})
    discarded = [
        str(item.get("title") or item.get("id") or "")
        for item in adaptive.get("hypotheses") or []
        if isinstance(item, dict) and item.get("status") == "discarded"
    ]
    memory.update(
        {
            "version": 2,
            "fingerprint_signature": fingerprint.get("signature"),
            "platform_family": (fingerprint.get("platform") or {}).get("family"),
            "init_system": fingerprint.get("init_system"),
            "virtualization": fingerprint.get("virtualization"),
            "causal_hypothesis_id": leader.get("id"),
            "causal_hypothesis_status": leader.get("status"),
            "causal_mechanism": leader.get("mechanism"),
            "negative_lessons": discarded[:10],
        }
    )
    return memory


def install_adaptive_reasoning() -> None:
    """Instala o motor adaptativo sobre o loop existente uma única vez."""
    global _INSTALLED, _ORIGINAL_MODEL_CALL, _ORIGINAL_RUN, _ORIGINAL_BUILD_MEMORY
    if _INSTALLED:
        return
    _ORIGINAL_MODEL_CALL = engine._model_call
    _ORIGINAL_RUN = engine.run_dynamic_investigation
    _ORIGINAL_BUILD_MEMORY = persistence.build_operational_memory

    engine._model_call = _adaptive_model_call
    engine.run_dynamic_investigation = _adaptive_run
    intelligent_agent.resilient_model_call = _adaptive_model_call
    persistence.build_operational_memory = _adaptive_memory
    operational_memory.build_operational_memory = _adaptive_memory
    _INSTALLED = True


__all__ = [
    "enrich_adaptive_prompt",
    "install_adaptive_reasoning",
]
