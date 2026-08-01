from __future__ import annotations

import math
import time
from collections import defaultdict
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator

from app.services.performance_config import get_performance_config


_LOCK = RLock()
_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_OBSERVATIONS: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = defaultdict(
    lambda: {"count": 0.0, "sum": 0.0, "max": 0.0}
)


def _enabled() -> bool:
    return get_performance_config().metrics_enabled


def _labels(values: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in (values or {}).items()))


def increment(name: str, amount: float = 1.0, *, labels: dict[str, Any] | None = None) -> None:
    if not _enabled():
        return
    with _LOCK:
        _COUNTERS[(str(name), _labels(labels))] += float(amount)


def observe(name: str, value: float, *, labels: dict[str, Any] | None = None) -> None:
    if not _enabled():
        return
    number = float(value)
    if not math.isfinite(number):
        return
    with _LOCK:
        row = _OBSERVATIONS[(str(name), _labels(labels))]
        row["count"] += 1.0
        row["sum"] += number
        row["max"] = max(row["max"], number)


@contextmanager
def timed(name: str, *, labels: dict[str, Any] | None = None) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    finally:
        observe(name, time.monotonic() - started, labels=labels)


def snapshot() -> dict[str, Any]:
    with _LOCK:
        counters = [
            {"name": name, "labels": dict(labels), "value": value}
            for (name, labels), value in sorted(_COUNTERS.items())
        ]
        observations = [
            {"name": name, "labels": dict(labels), **values}
            for (name, labels), values in sorted(_OBSERVATIONS.items())
        ]
    return {"counters": counters, "observations": observations}


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _label_text(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in labels) + "}"


def render_prometheus() -> str:
    lines = [
        "# HELP agent_info Informações estáticas do Agent IA.",
        "# TYPE agent_info gauge",
        'agent_info{component="agent-ia-interface"} 1',
    ]
    with _LOCK:
        for (name, labels), value in sorted(_COUNTERS.items()):
            metric = name if name.endswith("_total") else f"{name}_total"
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{metric}{_label_text(labels)} {value:g}")
        for (name, labels), values in sorted(_OBSERVATIONS.items()):
            lines.append(f"# TYPE {name} summary")
            label_text = _label_text(labels)
            lines.append(f"{name}_count{label_text} {values['count']:g}")
            lines.append(f"{name}_sum{label_text} {values['sum']:g}")
            lines.append(f"{name}_max{label_text} {values['max']:g}")
    return "\n".join(lines) + "\n"
