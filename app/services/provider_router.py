from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.settings import Settings, get_settings
from app.services.ai_providers import ProviderError
from app.services.provider_preflight import ProviderPreflight, preflight_all, preflight_provider
from app.services.provider_registry import provider_ids, provider_label


@dataclass(frozen=True)
class ProviderResolution:
    provider: str
    model: str
    label: str
    detail: str
    automatic: bool
    attempts: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "label": self.label,
            "detail": self.detail,
            "automatic": self.automatic,
            "attempts": [dict(item) for item in self.attempts],
        }


def automatic_provider_order(settings: Settings | None = None) -> tuple[str, ...]:
    settings = settings or get_settings()
    known = provider_ids(settings)
    raw = str(
        getattr(
            settings,
            "ai_auto_provider_order",
            "groq,omniroute,deepseek,gemini,ollama,openrouter",
        )
        or ""
    )
    ordered: list[str] = []
    for value in re.split(r"[,\n]", raw):
        provider = value.strip().lower()
        if provider in known and provider not in ordered:
            ordered.append(provider)
    for provider in known:
        if provider not in ordered:
            ordered.append(provider)
    return tuple(ordered)


def _attempt(result: ProviderPreflight, phase: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "provider": result.provider,
        "model": result.model,
        "state": result.state.value,
        "selectable": result.selectable,
        "detail": result.detail,
        "latency_ms": result.latency_ms,
    }


def resolve_automatic_provider(settings: Settings | None = None) -> ProviderResolution:
    """Seleciona e valida integralmente a primeira IA saudável da ordem configurada."""
    settings = settings or get_settings()
    if not bool(getattr(settings, "agent_autopilot_enabled", True)):
        raise ProviderError("O autopilot está desabilitado por AGENT_AUTOPILOT_ENABLED.")

    quick_rows = preflight_all(settings, quick=True)
    by_provider = {item.provider: item for item in quick_rows}
    attempts: list[dict[str, Any]] = []

    for provider in automatic_provider_order(settings):
        quick = by_provider.get(provider)
        if quick is None:
            continue
        attempts.append(_attempt(quick, "catalog"))
        if not quick.selectable:
            continue

        validated = preflight_provider(provider, settings, quick.model or None, quick=False)
        attempts.append(_attempt(validated, "full_preflight"))
        if not validated.selectable:
            continue

        detail = (
            f"Autopilot selecionou {validated.label} ({validated.model}) após "
            f"{sum(1 for item in attempts if item['phase'] == 'full_preflight')} validação(ões) completas."
        )
        return ProviderResolution(
            provider=validated.provider,
            model=validated.model,
            label=validated.label or provider_label(validated.provider, settings),
            detail=detail,
            automatic=True,
            attempts=tuple(attempts),
        )

    reasons = [
        f"{item['provider']}: {item['detail']}"
        for item in attempts
        if not item.get("selectable")
    ]
    summary = " | ".join(reasons[-8:]) or "nenhum provedor foi detectado"
    raise ProviderError(
        "Nenhuma IA passou na validação automática antes do SSH. "
        f"Diagnóstico: {summary}. Execute 'agent doctor ai'."
    )
