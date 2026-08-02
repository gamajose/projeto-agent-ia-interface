from __future__ import annotations

import json
import unicodedata
from typing import Any

from app.services import adaptive_hypotheses as base


_INSTALLED = False
_ORIGINAL_BUILD = base.build_adaptive_hypothesis_state


_DIRECT_SIGNALS: dict[str, tuple[str, ...]] = {
    "resource_exhaustion": (
        "no space left on device",
        "out of memory",
        "oom-killer",
        "killed process",
        "inode 100%",
        "filesystem 100%",
    ),
    "permission_or_ownership": (
        "permission denied",
        "operation not permitted",
        "read-only file system",
        "avc denied",
    ),
    "invalid_configuration": (
        "syntax error",
        "failed to parse",
        "unknown directive",
        "invalid configuration",
    ),
    "process_crash_loop": (
        "core dumped",
        "segmentation fault",
        "start request repeated too quickly",
    ),
    "snmp_identity_or_access_failure": (
        "authorizationerror",
        "authentication failure",
        "unknown community",
        "unknown user name",
    ),
}


def _normalize(value: Any) -> str:
    return (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )


def _evidence_blob(evidence: list[dict[str, Any]] | None) -> str:
    chunks: list[str] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        chunks.extend(
            (
                str(item.get("tool") or ""),
                str(item.get("command") or ""),
                str(item.get("stdout") or ""),
                str(item.get("stderr") or ""),
                json.dumps(item.get("normalized") or {}, ensure_ascii=False, default=str),
            )
        )
    return _normalize("\n".join(chunks))


def _apply_direct_certainty(
    state: dict[str, Any],
    evidence: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    blob = _evidence_blob(evidence)
    if not blob:
        return state

    candidates: list[dict[str, Any]] = []
    for item in state.get("hypotheses") or []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "")
        patterns = _DIRECT_SIGNALS.get(identifier) or ()
        matched = [pattern for pattern in patterns if pattern in blob]
        contradictions = list(item.get("contradicting_evidence") or [])
        if not matched or contradictions:
            continue
        item["status"] = "confirmed"
        item["band"] = "confirmed"
        item["score"] = max(95, int(item.get("score") or 0))
        item["hard_signals"] = list(dict.fromkeys([*(item.get("hard_signals") or []), *matched]))
        candidates.append(item)

    if not candidates:
        return state

    candidates.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
    confirmed = candidates[0]
    hypotheses = list(state.get("hypotheses") or [])
    hypotheses.sort(
        key=lambda item: (
            1 if isinstance(item, dict) and item.get("id") == confirmed.get("id") else 0,
            int(item.get("score") or 0) if isinstance(item, dict) else 0,
        ),
        reverse=True,
    )
    state["hypotheses"] = hypotheses
    state["confirmed_cause"] = confirmed
    state["leader"] = confirmed
    state["stop_decision"] = {
        "ready": True,
        "reason": (
            "A causa possui um sinal determinístico direto, atual e sem evidência contraditória. "
            "As hipóteses concorrentes deixam de ser exibidas como possibilidades equivalentes."
        ),
    }
    causal_chain = list(state.get("causal_chain") or [])
    symptom_rows = [item for item in causal_chain if isinstance(item, dict) and item.get("type") == "reported_symptom"]
    state["causal_chain"] = [
        {"type": "mechanism", "statement": str(confirmed.get("mechanism") or confirmed.get("title") or "Causa confirmada")},
        *symptom_rows,
    ]
    return state


def build_adaptive_hypothesis_state(**kwargs: Any) -> dict[str, Any]:
    state = _ORIGINAL_BUILD(**kwargs)
    return _apply_direct_certainty(state, kwargs.get("evidence"))


def enrich_analysis_with_hypotheses(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    adaptive = build_adaptive_hypothesis_state(
        objective=str(result.get("context") or ""),
        profile=result.get("profile"),
        evidence=list(result.get("evidence") or []),
        assessments=list(result.get("round_assessments") or []),
        previous_state=analysis.get("adaptive_hypotheses"),
        runtime_context=dict(result.get("runtime_context") or {}),
        similar_history=list(result.get("similar_history") or []),
    )
    analysis["adaptive_hypotheses"] = adaptive

    confirmed = adaptive.get("confirmed_cause") or {}
    probable = adaptive.get("leader") or {}
    current_root = dict(analysis.get("root_cause") or {})
    current_statement = str(current_root.get("statement") or analysis.get("probable_cause") or "").strip()
    if confirmed and not current_statement:
        analysis["probable_cause"] = confirmed.get("mechanism")
    analysis["dynamic_investigation"] = {
        "state": "cause_confirmed" if confirmed else "hypothesis_testing",
        "leader": confirmed or probable or None,
        "next_best_tests": adaptive.get("next_best_tests") or [],
        "stop_decision": adaptive.get("stop_decision") or {},
        "novelty": adaptive.get("novelty"),
    }
    result["adaptive_hypotheses"] = adaptive
    result["analysis"] = analysis
    return result


def install_certainty_rules() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base.build_adaptive_hypothesis_state = build_adaptive_hypothesis_state
    base.enrich_analysis_with_hypotheses = enrich_analysis_with_hypotheses
    _INSTALLED = True


__all__ = [
    "build_adaptive_hypothesis_state",
    "enrich_analysis_with_hypotheses",
    "install_certainty_rules",
]
