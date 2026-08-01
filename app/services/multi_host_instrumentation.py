from __future__ import annotations

from functools import wraps
from typing import Any

from app.services import multi_host_runner
from app.services.investigation_budget import allow_deep_dive, use_investigation_budget
from app.services.metrics import increment, observe
from app.services.multi_host_triage import triage_host
from app.services.nested_ssh import NestedSSHExecutor
from app.services.performance_config import get_performance_config


_INSTALLED = False


def _triage_result(
    *,
    target: str,
    environment: Any,
    triage: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    analysis = {
        "status": triage.get("status") or "inconclusive",
        "confidence": int(triage.get("confidence") or 0),
        "summary": triage.get("summary"),
        "facts": list(triage.get("facts") or []),
        "probable_cause": triage.get("probable_cause"),
        "conclusion": reason,
        "recommendations": list(triage.get("recommendations") or []),
        "triage_only": True,
    }
    environment_value = getattr(environment, "value", None) or str(environment or "unknown")
    return {
        "target": target,
        "hostname": triage.get("hostname"),
        "profile": "multi_host_triage",
        "status": analysis["status"],
        "confidence": analysis["confidence"],
        "environment_classification": {"environment": environment_value},
        "analysis": analysis,
        "evidence": list(triage.get("evidence") or []),
        "plans": [],
        "round_assessments": [],
        "ai_diagnostics": [],
        "duration_ms": int((triage.get("triage") or {}).get("duration_ms") or 0),
        "triage": triage.get("triage") or {},
    }


def install_multi_host_instrumentation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_dynamic = multi_host_runner.run_dynamic_investigation

    @wraps(original_dynamic)
    def triage_aware_dynamic(*args, **kwargs):
        executor = kwargs.get("executor")
        if not isinstance(executor, NestedSSHExecutor):
            return original_dynamic(*args, **kwargs)
        config = get_performance_config()
        if not config.triage_enabled:
            return original_dynamic(*args, **kwargs)

        target = str(kwargs.get("target") or executor.host)
        environment = kwargs.get("environment")
        context = str(kwargs.get("context") or "")
        triage = triage_host(
            executor,
            objective=context,
            environment=environment,
            label=target,
            timeout=config.triage_timeout_seconds,
        )
        score = int(triage.get("score") or 0)
        if score < 20:
            increment("agent_multi_host_triage", labels={"decision": "triage_only", "reason": "low_score"})
            return _triage_result(
                target=target,
                environment=environment,
                triage=triage,
                reason=(
                    "A triagem não encontrou evidência suficiente para consumir uma análise profunda neste host. "
                    "O resultado foi preservado como comparação com os demais servidores."
                ),
            )
        if not allow_deep_dive(executor.host):
            increment("agent_multi_host_triage", labels={"decision": "triage_only", "reason": "budget"})
            return _triage_result(
                target=target,
                environment=environment,
                triage=triage,
                reason=(
                    "O host apresentou indícios, mas o limite global de aprofundamentos foi atingido. "
                    "Revise a triagem e execute uma investigação focada se necessário."
                ),
            )

        increment("agent_multi_host_triage", labels={"decision": "deep_dive"})
        result = original_dynamic(*args, **kwargs)
        result["triage"] = triage.get("triage") or {}
        result["evidence"] = [*(triage.get("evidence") or []), *(result.get("evidence") or [])]
        analysis = dict(result.get("analysis") or {})
        analysis["facts"] = list(
            dict.fromkeys([*(triage.get("facts") or []), *(analysis.get("facts") or [])])
        )[:24]
        analysis["triage"] = triage.get("triage") or {}
        result["analysis"] = analysis
        return result

    multi_host_runner.run_dynamic_investigation = triage_aware_dynamic

    original = multi_host_runner.run_multi_host_tracked
    if getattr(original, "__agent_budgeted__", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(*args, **kwargs):
        with use_investigation_budget() as budget:
            result = original(*args, **kwargs)
            snapshot = budget.snapshot()
            result["budget"] = snapshot
            analysis = dict(result.get("analysis") or {})
            analysis["budget"] = snapshot
            result["analysis"] = analysis
            increment("agent_investigations", labels={"mode": "investigate", "multi_host": "true"})
            observe(
                "agent_investigation_duration_seconds",
                float(result.get("duration_ms") or 0) / 1000.0,
                labels={"multi_host": "true"},
            )
            return result

    setattr(wrapped, "__agent_budgeted__", True)
    multi_host_runner.run_multi_host_tracked = wrapped
    _INSTALLED = True
