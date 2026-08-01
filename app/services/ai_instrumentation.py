from __future__ import annotations

import time
from functools import wraps
from typing import Any, Callable

from app.services.ai_providers import GeminiProvider, OllamaProvider, OpenAICompatibleProvider
from app.services.investigation_budget import reserve_ai_call
from app.services.metrics import increment, observe


_INSTALLED = False


def _instrument(method: Callable) -> Callable:
    @wraps(method)
    def wrapped(self, prompt: str):
        provider = str(getattr(self, "name", "unknown") or "unknown")
        model = str(getattr(self, "model", "") or "default")
        reserve_ai_call(provider)
        started = time.monotonic()
        try:
            result, metadata = method(self, prompt)
            increment("agent_ai_results", labels={"provider": provider, "status": "success"})
            return result, {
                **dict(metadata or {}),
                "provider": provider,
                "model": model,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        except Exception:
            increment("agent_ai_results", labels={"provider": provider, "status": "failed"})
            raise
        finally:
            observe(
                "agent_ai_request_duration_seconds",
                time.monotonic() - started,
                labels={"provider": provider, "model": model},
            )

    setattr(wrapped, "__agent_instrumented__", True)
    return wrapped


def install_ai_instrumentation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for provider_class in (GeminiProvider, OpenAICompatibleProvider, OllamaProvider):
        current: Any = provider_class.generate_json
        if getattr(current, "__agent_instrumented__", False):
            continue
        provider_class.generate_json = _instrument(current)  # type: ignore[method-assign]
    _INSTALLED = True
