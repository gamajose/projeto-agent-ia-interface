from __future__ import annotations

import time
from typing import Any

from app.services.codex_cli import CodexCLIError, codex_cli_status, codex_generate_json
from app.services import provider_preflight as preflight


_INSTALLED = False


def _codex_result(settings: Any, model_name: str | None, *, quick: bool) -> preflight.ProviderPreflight:
    started = time.monotonic()
    model = str(model_name or getattr(settings, "codex_model", "") or "").strip()
    status = codex_cli_status(settings)
    if not status.available:
        return preflight.ProviderPreflight(
            provider="codex",
            label="OpenAI Codex CLI",
            state=preflight.ProviderState.NOT_CONFIGURED,
            model=model,
            detail=(
                "Codex CLI não encontrado. Configure CODEX_CLI_PATH ou mantenha o executável em "
                "~/ia/codex, ~/.local/bin ou no PATH."
            ),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            selectable=False,
        )
    if quick:
        return preflight.ProviderPreflight(
            provider="codex",
            label="OpenAI Codex CLI",
            state=preflight.ProviderState.AVAILABLE,
            model=model,
            detail=f"{status.version} detectado. A sessão autenticada do Codex CLI será usada na investigação.",
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            selectable=True,
            valid_routes=(model,) if model else (),
        )
    try:
        payload, _ = codex_generate_json(
            'Responda somente com o objeto JSON {"preflight":true}.',
            settings=settings,
            model=model or None,
        )
        if payload.get("preflight") is not True:
            raise CodexCLIError("resposta de preflight sem confirmação")
        return preflight.ProviderPreflight(
            provider="codex",
            label="OpenAI Codex CLI",
            state=preflight.ProviderState.AVAILABLE,
            model=model,
            detail="Codex CLI, autenticação e saída JSON validados.",
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            selectable=True,
            valid_routes=(model,) if model else (),
        )
    except Exception as exc:
        return preflight.ProviderPreflight(
            provider="codex",
            label="OpenAI Codex CLI",
            state=preflight.ProviderState.UNAVAILABLE,
            model=model,
            detail=f"Codex CLI foi localizado, mas não concluiu o teste: {type(exc).__name__}: {exc}",
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            selectable=False,
        )


def install_codex_provider_preflight() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = preflight.preflight_provider

    def wrapped(provider: str, settings: Any = None, model_name: str | None = None, *, quick: bool = False):
        normalized = str(provider or "").strip().lower()
        if normalized == "codex":
            settings = settings or preflight.get_settings()
            return _codex_result(settings, model_name, quick=quick)
        return original(provider, settings, model_name, quick=quick)

    preflight.preflight_provider = wrapped
    _INSTALLED = True
