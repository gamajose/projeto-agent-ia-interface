from __future__ import annotations

from typing import Any

from app.services.adaptive_incident_graph import group_related_alerts
from app.services.conclusion_validator import validate_conclusion
from app.services.incident_correlation import correlate_alerts
from app.services.incident_intelligence import (
    build_dependency_map,
    classify_access_failure,
    evidence_freshness,
)


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def enrich_incident_intelligence(result: dict[str, Any]) -> dict[str, Any]:
    """Aplica correlação, validade temporal e o estado causal adaptativo."""
    analysis = dict(result.get("analysis") or {})
    journey = [
        item
        for item in analysis.get("access_journey")
        or (result.get("connection") or {}).get("access_journey")
        or []
        if isinstance(item, dict)
    ]
    connection_failure = (result.get("connection") or {}).get("access_failure")
    if not isinstance(connection_failure, dict):
        connection_failure = None

    correlation = correlate_alerts(result)
    adaptive = dict(analysis.get("adaptive_hypotheses") or result.get("adaptive_hypotheses") or {})
    adaptive_grouping = group_related_alerts(
        objective=str(result.get("context") or ""),
        adaptive_state=adaptive,
        existing_correlation=correlation,
    )
    conclusion_validation = validate_conclusion(result)
    freshness = evidence_freshness(result)
    dependencies = build_dependency_map(result)

    confidence = max(0, min(100, int(analysis.get("confidence") or 0)))
    if conclusion_validation["verdict"] == "contradicted":
        confidence = min(confidence, 45)
        missing = list(analysis.get("missing_information") or [])
        missing.append("Reconciliar as contradições detectadas pela validação independente da conclusão.")
        analysis["missing_information"] = _unique(missing)
    elif conclusion_validation["verdict"] == "needs_more_evidence":
        confidence = min(confidence, 70)
    analysis["confidence"] = confidence
    analysis["adaptive_alert_grouping"] = adaptive_grouping

    intelligence = {
        "access_failure": connection_failure,
        "alert_correlation": correlation,
        "adaptive_alert_grouping": adaptive_grouping,
        "dependency_map": dependencies,
        "adaptive_dependency_graph": analysis.get("adaptive_dependency_graph"),
        "adaptive_hypotheses": adaptive,
        "environment_fingerprint": analysis.get("environment_fingerprint"),
        "conclusion_validation": conclusion_validation,
        "evidence_freshness": freshness,
        "access_journey_complete": bool(journey and journey[-1].get("status") == "completed"),
    }
    analysis["incident_intelligence"] = intelligence
    result["adaptive_alert_grouping"] = adaptive_grouping
    result["incident_intelligence"] = intelligence
    result["analysis"] = analysis
    return result


__all__ = ["classify_access_failure", "correlate_alerts", "enrich_incident_intelligence"]
