from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def stamp_evidence_timing(result: dict[str, Any]) -> dict[str, Any]:
    """Garante metadados temporais mínimos sem alterar a saída técnica coletada."""
    now = datetime.now(timezone.utc).isoformat()
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        return result
    for item in evidence:
        if not isinstance(item, dict):
            continue
        item.setdefault("collected_at", now)
        if item.get("duration_ms") is None and item.get("elapsed_seconds") is not None:
            try:
                item["duration_ms"] = max(0, int(float(item["elapsed_seconds"]) * 1000))
            except (TypeError, ValueError):
                item["duration_ms"] = None
        item.setdefault("source", "remote_collection")
        item.setdefault("target", result.get("target"))
    return result
