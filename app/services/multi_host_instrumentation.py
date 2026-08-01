from __future__ import annotations

from functools import wraps

from app.services import multi_host_runner
from app.services.investigation_budget import use_investigation_budget
from app.services.metrics import increment, observe


_INSTALLED = False


def install_multi_host_instrumentation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
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
