from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.core.settings import Settings, get_settings
from app.services import dynamic_agent as engine
from app.services import intelligent_agent
from app.services.ai_providers import (
    current_model_override,
    current_provider_override,
    get_provider,
    use_provider,
)
from app.services.operational_memory import playbook_effectiveness_bonus
from app.services.playbooks import Playbook, list_playbooks
from app.services.provider_preflight import preflight_provider
from app.services.provider_router import automatic_provider_order
from app.services.redaction import redact_object, redact_text


_INSTALLED = False
_ORIGINAL_MODEL_CALL = intelligent_agent.resilient_model_call
_ORIGINAL_SELECT_PLAYBOOK = engine.select_playbook
_ORIGINAL_PLAYBOOK_SUMMARY = engine.playbook_summary
_ORIGINAL_PREPARE_CORRECTIONS = engine._prepare_corrections

_PLAYBOOK_ENSEMBLE: ContextVar[list[dict[str, Any]]] = ContextVar(
    "agent_playbook_ensemble",
    default=[],
)
_PLAYBOOK_PROFILE: ContextVar[str] = ContextVar("agent_playbook_ensemble_profile", default="unknown")


@dataclass(frozen=True)
class EnsembleConfig:
    enabled: bool
    size: int
    min_success: int
    playbook_limit: int


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on", "sim", "s"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def get_ensemble_config() -> EnsembleConfig:
    return EnsembleConfig(
        enabled=_bool("AGENT_ENSEMBLE_ENABLED", True),
        size=_int("AGENT_ENSEMBLE_SIZE", 3, 1, 5),
        min_success=_int("AGENT_ENSEMBLE_MIN_SUCCESS", 2, 1, 3),
        playbook_limit=_int("AGENT_PLAYBOOK_ENSEMBLE_LIMIT", 4, 1, 8),
    )


def _generic_objective(objective: str) -> bool:
    text = str(objective or "").strip().casefold()
    return not text or text in {
        "validar a saúde geral do servidor",
        "validar a saude geral do servidor",
        "analisar o ambiente",
        "análise geral",
        "analise geral",
    }


def _rank_playbooks(objective: str, profile: str, *, limit: int) -> list[tuple[int, Playbook]]:
    ranked: list[tuple[int, Playbook]] = []
    generic = _generic_objective(objective)
    for playbook in list_playbooks():
        score = playbook.score(objective, profile)
        if score < 0 and generic:
            if playbook.profiles and profile not in playbook.profiles and "any" not in playbook.profiles:
                continue
            score = int(playbook.priority) + (20 if profile in playbook.profiles else 0)
        if score < 0:
            continue
        score += playbook_effectiveness_bonus(playbook.id, profile)
        ranked.append((score, playbook))
    ranked.sort(key=lambda item: (item[0], item[1].priority, item[1].id), reverse=True)
    return ranked[: max(1, limit)]


def _ensemble_select_playbook(objective: str, profile: str) -> Playbook | None:
    config = get_ensemble_config()
    primary = _ORIGINAL_SELECT_PLAYBOOK(objective, profile)
    ranked = _rank_playbooks(objective, profile, limit=config.playbook_limit)

    if primary and all(item[1].id != primary.id for item in ranked):
        ranked.insert(0, (10_000, primary))
        ranked = ranked[: config.playbook_limit]

    members = [
        {
            "id": playbook.id,
            "title": playbook.title,
            "score": score,
            "profiles": list(playbook.profiles),
            "allowed_corrections": list(playbook.allowed_corrections),
            "source": playbook.source,
            "primary": bool(primary and playbook.id == primary.id),
        }
        for score, playbook in ranked
    ]
    _PLAYBOOK_ENSEMBLE.set(members)
    _PLAYBOOK_PROFILE.set(profile or "unknown")
    return primary


def _ensemble_playbook_summary(playbook: Playbook | None) -> dict[str, Any] | None:
    summary = _ORIGINAL_PLAYBOOK_SUMMARY(playbook)
    members = _PLAYBOOK_ENSEMBLE.get()
    if not members:
        return summary
    base = dict(summary or {})
    base.setdefault("id", playbook.id if playbook else "ensemble-advisory")
    base.setdefault("title", playbook.title if playbook else "Ensemble consultivo de playbooks")
    base.setdefault("allowed_corrections", list(playbook.allowed_corrections) if playbook else [])
    base["ensemble"] = {
        "enabled": True,
        "method": "weighted_rank_with_operational_memory",
        "members": members,
        "correction_scope": "primary_or_post_evidence_match",
    }
    return base


def _correction_playbook_from_evidence(analysis: dict[str, Any]) -> Playbook | None:
    profile = _PLAYBOOK_PROFILE.get() or "unknown"
    objective = "\n".join(
        [
            str(analysis.get("probable_cause") or ""),
            str(analysis.get("conclusion") or ""),
            *(str(item) for item in (analysis.get("facts") or [])[:12]),
            *(str(item) for item in (analysis.get("recommendations") or [])[:8]),
        ]
    )
    for _score, playbook in _rank_playbooks(objective, profile, limit=8):
        if playbook.allowed_corrections:
            return playbook
    return None


def _ensemble_prepare_corrections(
    analysis: dict[str, Any],
    evidence: list[dict[str, Any]],
    playbook: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if list((playbook or {}).get("allowed_corrections") or []):
        return _ORIGINAL_PREPARE_CORRECTIONS(analysis, evidence, playbook)

    selected = _correction_playbook_from_evidence(analysis)
    if not selected:
        return _ORIGINAL_PREPARE_CORRECTIONS(analysis, evidence, playbook)

    selected_summary = _ORIGINAL_PLAYBOOK_SUMMARY(selected) or {
        "id": selected.id,
        "title": selected.title,
        "allowed_corrections": list(selected.allowed_corrections),
    }
    actions, diagnostics = _ORIGINAL_PREPARE_CORRECTIONS(analysis, evidence, selected_summary)
    diagnostics = {
        **dict(diagnostics or {}),
        "playbook_reselected_after_evidence": True,
        "correction_playbook": selected.id,
        "correction_playbook_title": selected.title,
        "allowed_corrections": list(selected.allowed_corrections),
    }
    return actions, diagnostics


def _provider_names(provider_name: str | None, settings: Settings, size: int) -> list[tuple[str, str | None, str]]:
    selected = str(
        provider_name
        or current_provider_override()
        or settings.ai_provider
        or "gemini"
    ).strip().casefold()
    selected_model = current_model_override()

    names: list[tuple[str, str | None, str]] = []
    if selected and selected != "auto":
        names.append((selected, selected_model, "selected"))
    for candidate in automatic_provider_order(settings):
        candidate = str(candidate or "").strip().casefold()
        if not candidate or candidate == "auto" or any(item[0] == candidate for item in names):
            continue
        names.append((candidate, None, "ensemble"))
    return names[: max(1, size)]


def _call_member(
    provider_name: str,
    requested_model: str | None,
    source: str,
    prompt: str,
    purpose: str,
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model = requested_model
    if source != "selected" or not model:
        preflight = preflight_provider(provider_name, settings, model, quick=False)
        if not preflight.selectable:
            raise RuntimeError(preflight.detail)
        model = model or preflight.model or None

    with use_provider(provider_name, model):
        provider = get_provider(provider_name, settings, model)
        result, metadata = provider.generate_json(redact_text(prompt))
        intelligent_agent._validate_reasoning_output(purpose, result)
        clean = dict(result)
        clean["_ai_provider"] = provider.name
        clean["_ai_model"] = provider.model
        return clean, {
            "provider": provider.name,
            "model": provider.model,
            "source": source,
            "status": "success",
            **dict(metadata or {}),
        }


def _confidence(result: dict[str, Any], purpose: str) -> int:
    key = "initial_confidence" if purpose == "mission_interpretation" else "confidence"
    try:
        return int(result.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _fallback_consensus(outputs: list[dict[str, Any]], purpose: str) -> dict[str, Any]:
    if purpose == "final_analysis":
        counts: dict[str, int] = {}
        for item in outputs:
            status = str(item.get("status") or "inconclusive")
            counts[status] = counts.get(status, 0) + 1
        majority = max(counts, key=lambda key: (counts[key], max(_confidence(item, purpose) for item in outputs if str(item.get("status") or "inconclusive") == key)))
        pool = [item for item in outputs if str(item.get("status") or "inconclusive") == majority]
        return max(pool, key=lambda item: _confidence(item, purpose))
    return max(outputs, key=lambda item: _confidence(item, purpose))


def _correction_consensus(outputs: list[dict[str, Any]], minimum_votes: int) -> dict[str, Any]:
    votes: dict[str, dict[str, Any]] = {}
    for output in outputs:
        seen: set[str] = set()
        for action in output.get("actions") or []:
            if not isinstance(action, dict):
                continue
            key = json.dumps(
                {
                    "tool": str(action.get("tool") or ""),
                    "arguments": action.get("arguments") or {},
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if key in seen:
                continue
            seen.add(key)
            row = votes.setdefault(key, {"count": 0, "action": action})
            row["count"] += 1
    accepted = [
        {**dict(row["action"]), "ensemble_votes": row["count"], "ensemble_required_votes": minimum_votes}
        for row in votes.values()
        if int(row["count"]) >= minimum_votes
    ]
    return {"actions": accepted}


def _judge(
    prompt: str,
    purpose: str,
    outputs: list[dict[str, Any]],
    member: dict[str, Any],
    settings: Settings,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    candidates = [
        {key: value for key, value in item.items() if not str(key).startswith("_ai_")}
        for item in outputs
    ]
    judge_prompt = (
        "Você é o agregador de um método Ensemble para AIOps. Responda somente JSON válido.\n"
        "Produza UMA decisão que respeite exatamente o contrato pedido na solicitação original.\n"
        "Use somente afirmações sustentadas pelos candidatos. Quando houver divergência, prefira a conclusão mais conservadora e verificável.\n"
        "Não invente fatos, comandos, evidências ou permissões.\n\n"
        "SOLICITAÇÃO ORIGINAL:\n"
        + redact_text(prompt[-18000:])
        + "\n\nRESPOSTAS DOS MEMBROS DO ENSEMBLE:\n"
        + json.dumps(redact_object(candidates), ensure_ascii=False, default=str)[:24000]
    )
    provider_name = str(member.get("provider") or "")
    model = str(member.get("model") or "") or None
    try:
        with use_provider(provider_name, model):
            provider = get_provider(provider_name, settings, model)
            result, metadata = provider.generate_json(judge_prompt)
            intelligent_agent._validate_reasoning_output(purpose, result)
            result["_ai_provider"] = provider.name
            result["_ai_model"] = provider.model
            return result, {
                "provider": provider.name,
                "model": provider.model,
                "status": "success",
                **dict(metadata or {}),
            }
    except Exception as exc:
        return None, {
            "provider": provider_name,
            "model": model or "",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def ensemble_model_call(
    prompt: str,
    purpose: str,
    provider_name: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    config = get_ensemble_config()
    if not config.enabled or config.size <= 1 or purpose == "final_critic":
        return _ORIGINAL_MODEL_CALL(prompt, purpose, provider_name)

    settings = get_settings()
    diagnostics: dict[str, Any] = {
        "purpose": purpose,
        "success": False,
        "ensemble": True,
        "method": "multi_provider_vote_then_adjudicate",
        "members_requested": config.size,
        "minimum_success": config.min_success,
        "attempts": [],
    }
    outputs: list[dict[str, Any]] = []
    successful_members: list[dict[str, Any]] = []

    for candidate, model, source in _provider_names(provider_name, settings, config.size):
        try:
            output, attempt = _call_member(candidate, model, source, prompt, purpose, settings)
            outputs.append(output)
            successful_members.append(attempt)
            diagnostics["attempts"].append(attempt)
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

    if not outputs:
        diagnostics["error"] = "nenhum membro do Ensemble retornou uma resposta válida"
        trace = intelligent_agent._REASONING_TRACE.get()
        if trace is not None:
            trace.append(redact_object(diagnostics))
        return None, diagnostics

    required = min(config.min_success, len(outputs))
    diagnostics["valid_members"] = len(outputs)
    diagnostics["consensus_required"] = required

    if purpose == "correction_planning":
        result = _correction_consensus(outputs, required)
        result["_ai_provider"] = "ensemble"
        result["_ai_model"] = "+".join(str(item.get("model") or item.get("provider")) for item in successful_members)
        diagnostics.update(
            {
                "success": True,
                "provider": "ensemble",
                "model": result["_ai_model"],
                "consensus": "strict_action_vote",
                "accepted_actions": len(result.get("actions") or []),
            }
        )
    elif len(outputs) >= 2:
        judged, judge_diag = _judge(prompt, purpose, outputs, successful_members[0], settings)
        diagnostics["adjudicator"] = judge_diag
        result = judged or _fallback_consensus(outputs, purpose)
        diagnostics.update(
            {
                "success": True,
                "provider": str(result.get("_ai_provider") or "ensemble"),
                "model": str(result.get("_ai_model") or "consensus"),
                "consensus": "adjudicated" if judged else "deterministic_fallback",
            }
        )
    else:
        result = outputs[0]
        diagnostics.update(
            {
                "success": True,
                "provider": str(result.get("_ai_provider") or successful_members[0].get("provider") or ""),
                "model": str(result.get("_ai_model") or successful_members[0].get("model") or ""),
                "consensus": "single_valid_member",
            }
        )

    trace = intelligent_agent._REASONING_TRACE.get()
    if trace is not None:
        trace.append(redact_object(diagnostics))
    return result, diagnostics


def install_ensemble_reasoning() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    intelligent_agent.resilient_model_call = ensemble_model_call
    engine._model_call = ensemble_model_call
    engine.select_playbook = _ensemble_select_playbook
    engine.playbook_summary = _ensemble_playbook_summary
    engine._prepare_corrections = _ensemble_prepare_corrections
    _INSTALLED = True
