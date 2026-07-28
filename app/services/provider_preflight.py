from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.core.settings import Settings, get_settings
from app.services.ai_providers import (
    PROVIDER_LABELS,
    ProviderError,
    _default_omniroute_route,
    _secret,
    current_model_override,
    current_provider_override,
    omniroute_route_options,
    parse_json,
)


class ProviderState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    MISCONFIGURED = "misconfigured"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"


STATE_LABELS: dict[ProviderState, str] = {
    ProviderState.AVAILABLE: "disponível",
    ProviderState.UNAVAILABLE: "indisponível",
    ProviderState.MISCONFIGURED: "configuração inválida",
    ProviderState.DEGRADED: "degradado",
    ProviderState.NOT_CONFIGURED: "não configurado",
}


class ProviderPreflight(BaseModel):
    provider: str
    label: str
    state: ProviderState
    model: str = ""
    detail: str
    latency_ms: int | None = Field(default=None, ge=0)
    selectable: bool = False
    valid_routes: tuple[str, ...] = ()
    invalid_routes: tuple[str, ...] = ()

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


_PROBE_PROMPT = 'Responda somente com o objeto JSON {"preflight":true}.'
_GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
_AUTO_MODEL_VALUES = {"", "auto", "auto-free", "latest-free"}
_TRANSIENT_GEMINI_STATUSES = {429, 500, 502, 503, 504}


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _timeout(settings: Settings) -> float:
    return float(getattr(settings, "ai_preflight_timeout_seconds", 8.0))


def _ollama_timeout(settings: Settings) -> float:
    return float(getattr(settings, "ollama_preflight_timeout_seconds", 60.0))


def _csv_values(value: str | None) -> tuple[str, ...]:
    return tuple(
        item
        for item in (part.strip() for part in re.split(r"[,\n]", value or ""))
        if item
    )


def _result(
    provider: str,
    *,
    state: ProviderState,
    model: str,
    detail: str,
    started: float | None = None,
    selectable: bool | None = None,
    valid_routes: tuple[str, ...] = (),
    invalid_routes: tuple[str, ...] = (),
) -> ProviderPreflight:
    return ProviderPreflight(
        provider=provider,
        label=PROVIDER_LABELS[provider],
        state=state,
        model=model,
        detail=detail,
        latency_ms=_elapsed_ms(started) if started is not None else None,
        selectable=state == ProviderState.AVAILABLE if selectable is None else selectable,
        valid_routes=valid_routes,
        invalid_routes=invalid_routes,
    )


def _safe_http_message(response: httpx.Response) -> str:
    """Extrai apenas a mensagem pública da API, sem cabeçalhos ou credenciais."""
    try:
        payload = response.json()
    except Exception:
        return ""
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif error:
            message = str(error)
        if not message:
            message = str(payload.get("message") or "")
    message = " ".join(message.split())
    return message[:300]


def _http_failure(provider: str, model: str, exc: Exception, started: float) -> ProviderPreflight:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        message = _safe_http_message(exc.response)
        suffix = f" {message}" if message else ""
        if status in {400, 401, 403, 404, 422}:
            return _result(
                provider,
                state=ProviderState.MISCONFIGURED,
                model=model,
                detail=f"A API recusou a configuração (HTTP {status}).{suffix}",
                started=started,
            )
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail=f"O serviço respondeu com erro HTTP {status}.{suffix}",
            started=started,
        )
    if isinstance(exc, httpx.TimeoutException):
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail="Tempo limite excedido ao consultar o serviço.",
            started=started,
        )
    if isinstance(exc, httpx.RequestError):
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail="Não foi possível conectar ao endpoint configurado.",
            started=started,
        )
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return _result(
            provider,
            state=ProviderState.DEGRADED,
            model=model,
            detail="O serviço respondeu, mas não retornou JSON válido no formato esperado.",
            started=started,
        )
    return _result(
        provider,
        state=ProviderState.UNAVAILABLE,
        model=model,
        detail=f"Falha inesperada durante o diagnóstico ({type(exc).__name__}).",
        started=started,
    )


def _ollama_models(settings: Settings) -> tuple[str, ...]:
    tags = httpx.get(
        f"{settings.ollama_base_url.rstrip('/')}/api/tags",
        timeout=_timeout(settings),
    )
    tags.raise_for_status()
    payload = tags.json()
    models = {
        str(value).strip()
        for item in payload.get("models", [])
        if isinstance(item, dict)
        for value in (item.get("name"), item.get("model"))
        if value
    }
    return tuple(sorted(model for model in models if model))


def _select_ollama_model(
    settings: Settings,
    installed: tuple[str, ...],
    model_name: str | None,
) -> tuple[str, str | None]:
    requested = (model_name or "").strip()
    configured = (settings.ollama_model or "").strip()
    explicit = model_name is not None and bool(requested)

    if explicit:
        return requested, None

    if configured and configured.casefold() not in _AUTO_MODEL_VALUES and configured in installed:
        return configured, None

    if not getattr(settings, "ollama_auto_fallback", True):
        return configured, None

    preferences = _csv_values(getattr(settings, "ollama_preferred_models", ""))
    selected = next((candidate for candidate in preferences if candidate in installed), "")
    if not selected and installed:
        selected = installed[0]

    fallback_from = (
        configured
        if configured and configured.casefold() not in _AUTO_MODEL_VALUES and configured != selected
        else None
    )
    return selected, fallback_from


def _probe_ollama(
    settings: Settings,
    model_name: str | None = None,
    *,
    quick: bool = False,
) -> ProviderPreflight:
    configured = (model_name or settings.ollama_model or "").strip()
    started = time.monotonic()
    try:
        installed = _ollama_models(settings)
        if not installed:
            return _result(
                "ollama",
                state=ProviderState.MISCONFIGURED,
                model=configured,
                detail="O serviço Ollama respondeu, mas não possui nenhum modelo instalado.",
                started=started,
            )

        model, fallback_from = _select_ollama_model(settings, installed, model_name)
        if not model or model not in installed:
            return _result(
                "ollama",
                state=ProviderState.MISCONFIGURED,
                model=model or configured,
                detail=(
                    f"O serviço respondeu, mas o modelo exato '{model or configured}' não está instalado. "
                    f"Modelos detectados: {', '.join(installed)}."
                ),
                started=started,
                valid_routes=installed,
                invalid_routes=((model or configured),) if (model or configured) else (),
            )

        if quick:
            if fallback_from:
                return _result(
                    "ollama",
                    state=ProviderState.DEGRADED,
                    model=model,
                    detail=(
                        f"O modelo configurado '{fallback_from}' não está instalado; "
                        f"o modelo local '{model}' foi detectado e será validado ao iniciar a investigação."
                    ),
                    started=started,
                    selectable=True,
                    valid_routes=installed,
                    invalid_routes=(fallback_from,),
                )
            return _result(
                "ollama",
                state=ProviderState.AVAILABLE,
                model=model,
                detail=(
                    f"Serviço e modelo local '{model}' detectados. "
                    "A geração será validada antes de abrir o SSH."
                ),
                started=started,
                valid_routes=installed,
            )

        try:
            response = httpx.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json={
                    "model": model,
                    "prompt": _PROBE_PROMPT,
                    "stream": False,
                    "format": "json",
                },
                timeout=_ollama_timeout(settings),
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            timeout = _ollama_timeout(settings)
            return _result(
                "ollama",
                state=ProviderState.UNAVAILABLE,
                model=model,
                detail=(
                    f"O modelo local '{model}' foi encontrado, mas não respondeu em {timeout:.0f}s. "
                    "O primeiro carregamento pode ser mais lento; ajuste "
                    "OLLAMA_PREFLIGHT_TIMEOUT_SECONDS se necessário."
                ),
                started=started,
                valid_routes=installed,
            )

        generated = response.json()
        parsed = parse_json(str(generated.get("response") or ""))
        if parsed.get("preflight") is not True:
            raise ValueError("resposta de preflight sem confirmação")

        if fallback_from:
            return _result(
                "ollama",
                state=ProviderState.DEGRADED,
                model=model,
                detail=(
                    f"O modelo configurado '{fallback_from}' não está instalado; "
                    f"o Agent selecionou automaticamente '{model}'. API e resposta JSON validadas."
                ),
                started=started,
                selectable=True,
                valid_routes=installed,
                invalid_routes=(fallback_from,),
            )
        return _result(
            "ollama",
            state=ProviderState.AVAILABLE,
            model=model,
            detail="API, modelo e resposta JSON validados.",
            started=started,
            valid_routes=installed,
        )
    except Exception as exc:
        return _http_failure("ollama", configured, exc, started)


def _configured_omniroute_routes(settings: Settings) -> tuple[str, ...]:
    return tuple(route.model for route in omniroute_route_options(settings))


def _probe_omniroute(settings: Settings, model_name: str | None = None) -> ProviderPreflight:
    try:
        token = _secret(settings, "OMNIROUTE_API_KEY", "omniroute_api_key")
    except Exception:
        return _result(
            "omniroute",
            state=ProviderState.UNAVAILABLE,
            model=(model_name or _default_omniroute_route(settings)),
            detail="Não foi possível consultar o backend de segredos do OmniRoute.",
        )
    selected_model = (model_name or _default_omniroute_route(settings)).strip()
    configured_routes = _configured_omniroute_routes(settings)
    if not token:
        return _result(
            "omniroute",
            state=ProviderState.NOT_CONFIGURED,
            model=selected_model,
            detail="Falta o token local do endpoint (OMNIROUTE_API_KEY).",
        )
    if not selected_model and not configured_routes:
        return _result(
            "omniroute",
            state=ProviderState.MISCONFIGURED,
            model="",
            detail="Configure OMNIROUTE_DEFAULT_ROUTE ou ao menos uma rota em OMNIROUTE_ROUTES.",
        )

    started = time.monotonic()
    try:
        response = httpx.get(
            f"{settings.omniroute_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_timeout(settings),
        )
        response.raise_for_status()
        payload = response.json()
        available_models = {
            str(item.get("id") or "").strip()
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
        candidates = (selected_model,) if model_name and selected_model else configured_routes
        if selected_model and selected_model not in candidates:
            candidates = (selected_model, *candidates)
        valid = tuple(route for route in candidates if route in available_models)
        invalid = tuple(route for route in candidates if route not in available_models)

        if selected_model and selected_model not in available_models:
            return _result(
                "omniroute",
                state=ProviderState.MISCONFIGURED,
                model=selected_model,
                detail=f"A rota/modelo '{selected_model}' não existe no gateway.",
                started=started,
                valid_routes=valid,
                invalid_routes=invalid,
            )
        if not valid:
            return _result(
                "omniroute",
                state=ProviderState.MISCONFIGURED,
                model=selected_model,
                detail="Nenhuma rota configurada no Agent existe no gateway.",
                started=started,
                valid_routes=valid,
                invalid_routes=invalid,
            )
        if invalid:
            return _result(
                "omniroute",
                state=ProviderState.DEGRADED,
                model=selected_model or valid[0],
                detail=f"{len(valid)} rota(s) válida(s); {len(invalid)} rota(s) ausente(s) no gateway.",
                started=started,
                selectable=True,
                valid_routes=valid,
                invalid_routes=invalid,
            )
        return _result(
            "omniroute",
            state=ProviderState.AVAILABLE,
            model=selected_model or valid[0],
            detail="Token, endpoint e rotas configuradas foram validados.",
            started=started,
            valid_routes=valid,
        )
    except Exception as exc:
        return _http_failure("omniroute", selected_model, exc, started)


def _normalize_gemini_model(value: str | None) -> str:
    model = (value or "").strip()
    return model.removeprefix("models/")


def _gemini_free_candidates(settings: Settings) -> tuple[str, ...]:
    configured = _csv_values(getattr(settings, "gemini_free_models", ""))
    if configured:
        return tuple(_normalize_gemini_model(item) for item in configured)
    return (
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    )


def _gemini_models(api_key: str, settings: Settings) -> tuple[str, ...]:
    models: list[str] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        response = httpx.get(
            f"{_GEMINI_API_ROOT}/models",
            headers={"x-goog-api-key": api_key},
            params=params,
            timeout=_timeout(settings),
        )
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("models", []):
            if not isinstance(item, dict):
                continue
            methods = {str(value) for value in item.get("supportedGenerationMethods", [])}
            if "generateContent" not in methods:
                continue
            name = _normalize_gemini_model(str(item.get("name") or ""))
            if name and name not in models:
                models.append(name)
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break
    return tuple(models)


def _select_gemini_model(
    settings: Settings,
    available: tuple[str, ...],
    model_name: str | None,
) -> tuple[str, str | None, tuple[str, ...], tuple[str, ...]]:
    requested = _normalize_gemini_model(model_name)
    configured = _normalize_gemini_model(settings.gemini_model)
    free_candidates = _gemini_free_candidates(settings)
    valid_free = tuple(candidate for candidate in free_candidates if candidate in available)
    invalid_free = tuple(candidate for candidate in free_candidates if candidate not in available)

    if model_name is not None and requested:
        return requested, None, valid_free, invalid_free

    configured_is_auto = configured.casefold() in _AUTO_MODEL_VALUES
    if getattr(settings, "gemini_auto_free", True):
        selected = valid_free[0] if valid_free else ""
        fallback_from = (
            configured
            if configured and not configured_is_auto and configured != selected
            else None
        )
        return selected or configured, fallback_from, valid_free, invalid_free

    return configured, None, valid_free, invalid_free


def _gemini_probe_candidates(
    settings: Settings,
    selected: str,
    valid_free: tuple[str, ...],
    model_name: str | None,
) -> tuple[str, ...]:
    ordered: list[str] = []
    if selected:
        ordered.append(selected)

    allow_fallback = bool(getattr(settings, "gemini_transient_fallback", True))
    if allow_fallback:
        for candidate in valid_free:
            if candidate and candidate not in ordered:
                ordered.append(candidate)

    if model_name is not None and not allow_fallback:
        return tuple(ordered[:1])
    return tuple(ordered)


def _probe_gemini(
    settings: Settings,
    model_name: str | None = None,
    *,
    quick: bool = False,
) -> ProviderPreflight:
    try:
        api_key = _secret(settings, "GEMINI_API_KEY", "gemini_api_key")
    except Exception:
        return _result(
            "gemini",
            state=ProviderState.UNAVAILABLE,
            model=_normalize_gemini_model(model_name or settings.gemini_model),
            detail="Não foi possível consultar o backend de segredos do Gemini.",
        )

    configured = _normalize_gemini_model(model_name or settings.gemini_model)
    if not api_key:
        return _result(
            "gemini",
            state=ProviderState.NOT_CONFIGURED,
            model=configured,
            detail="Falta a credencial GEMINI_API_KEY.",
        )

    started = time.monotonic()
    try:
        available = _gemini_models(api_key, settings)
        model, fallback_from, valid_free, invalid_free = _select_gemini_model(
            settings,
            available,
            model_name,
        )
        if not model or model not in available:
            detected = ", ".join(valid_free) or "nenhum modelo gratuito conhecido"
            return _result(
                "gemini",
                state=ProviderState.MISCONFIGURED,
                model=model or configured,
                detail=(
                    f"O modelo '{model or configured}' não aparece para esta chave na API Gemini. "
                    f"Modelos gratuitos compatíveis detectados: {detected}."
                ),
                started=started,
                valid_routes=valid_free,
                invalid_routes=invalid_free,
            )

        if quick:
            if fallback_from:
                detail = (
                    f"O modelo configurado '{fallback_from}' não aparece para esta chave; "
                    f"o modelo gratuito '{model}' foi detectado e será validado ao iniciar."
                )
                state = ProviderState.DEGRADED
            else:
                detail = (
                    f"Credencial e modelo gratuito '{model}' detectados. "
                    "A geração será validada antes de abrir o SSH."
                )
                state = ProviderState.AVAILABLE
            return _result(
                "gemini",
                state=state,
                model=model,
                detail=detail,
                started=started,
                selectable=True,
                valid_routes=valid_free or (model,),
                invalid_routes=invalid_free,
            )

        transient_failures: list[tuple[str, int, str]] = []
        candidates = _gemini_probe_candidates(settings, model, valid_free, model_name)
        for candidate in candidates:
            try:
                response = httpx.post(
                    f"{_GEMINI_API_ROOT}/models/{quote(candidate, safe='')}:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json={
                        "contents": [{"parts": [{"text": _PROBE_PROMPT}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                    timeout=_timeout(settings),
                )
                response.raise_for_status()
                payload = response.json()
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
                parsed = parse_json(str(text or ""))
                if parsed.get("preflight") is not True:
                    raise ValueError("resposta de preflight sem confirmação")
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in _TRANSIENT_GEMINI_STATUSES and len(candidates) > 1:
                    transient_failures.append(
                        (candidate, status, _safe_http_message(exc.response))
                    )
                    continue
                raise

            if transient_failures:
                failed_summary = ", ".join(
                    f"{failed_model} (HTTP {status})"
                    for failed_model, status, _ in transient_failures
                )
                detail = (
                    f"{failed_summary} apresentou indisponibilidade temporária; "
                    f"fallback automático para o modelo gratuito '{candidate}' validado."
                )
            elif fallback_from:
                detail = (
                    f"O modelo configurado '{fallback_from}' não está disponível para esta chave; "
                    f"o Agent selecionou automaticamente o modelo gratuito '{candidate}'."
                )
            elif configured.casefold() in _AUTO_MODEL_VALUES:
                detail = f"Modelo gratuito selecionado automaticamente: '{candidate}'."
            else:
                detail = "Credencial, modelo gratuito e resposta JSON validados."

            return _result(
                "gemini",
                state=ProviderState.AVAILABLE,
                model=candidate,
                detail=detail,
                started=started,
                valid_routes=valid_free or (candidate,),
                invalid_routes=invalid_free,
            )

        failures = ", ".join(
            f"{failed_model} (HTTP {status})"
            for failed_model, status, _ in transient_failures
        )
        public_message = next(
            (message for _, _, message in reversed(transient_failures) if message),
            "",
        )
        suffix = f" {public_message}" if public_message else ""
        return _result(
            "gemini",
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail=(
                "Todos os modelos gratuitos disponíveis responderam com "
                f"indisponibilidade temporária: {failures}.{suffix}"
            ),
            started=started,
            valid_routes=valid_free,
            invalid_routes=invalid_free,
        )
    except Exception as exc:
        return _http_failure("gemini", configured, exc, started)


def _direct_configuration(settings: Settings, provider: str) -> tuple[str | None, str, str]:
    if provider == "groq":
        return _secret(settings, "GROQ_API_KEY", "groq_api_key"), settings.groq_model, settings.groq_base_url
    if provider == "openrouter":
        return (
            _secret(settings, "OPENROUTER_API_KEY", "openrouter_api_key"),
            settings.openrouter_model,
            settings.openrouter_base_url,
        )
    raise ProviderError(f"Provedor direto desconhecido: {provider}.")


def _probe_direct(settings: Settings, provider: str, model_name: str | None = None) -> ProviderPreflight:
    try:
        api_key, configured_model, base_url = _direct_configuration(settings, provider)
    except Exception:
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model_name or "",
            detail="Não foi possível consultar o backend de segredos do provedor.",
        )
    model = (model_name or configured_model or "").strip()
    if not api_key:
        return _result(
            provider,
            state=ProviderState.NOT_CONFIGURED,
            model=model,
            detail=f"Falta a credencial {provider.upper()}_API_KEY.",
        )
    if not model:
        return _result(
            provider,
            state=ProviderState.MISCONFIGURED,
            model="",
            detail="O modelo do provedor não está configurado.",
        )

    started = time.monotonic()
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        if provider == "openrouter":
            headers["X-Title"] = settings.openrouter_app_name
            if settings.openrouter_site_url:
                headers["HTTP-Referer"] = settings.openrouter_site_url
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": _PROBE_PROMPT}],
                "temperature": 0,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            timeout=_timeout(settings),
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        parsed = parse_json(str(text or ""))
        if parsed.get("preflight") is not True:
            raise ValueError("resposta de preflight sem confirmação")
        return _result(
            provider,
            state=ProviderState.AVAILABLE,
            model=model,
            detail="Credencial, modelo e resposta JSON validados.",
            started=started,
        )
    except Exception as exc:
        return _http_failure(provider, model, exc, started)


def preflight_provider(
    provider: str,
    settings: Settings | None = None,
    model_name: str | None = None,
    *,
    quick: bool = False,
) -> ProviderPreflight:
    settings = settings or get_settings()
    normalized = provider.strip().lower()
    if normalized == "gemini":
        return _probe_gemini(settings, model_name, quick=quick)
    if normalized == "ollama":
        return _probe_ollama(settings, model_name, quick=quick)
    if normalized == "omniroute":
        return _probe_omniroute(settings, model_name)
    if normalized in {"groq", "openrouter"}:
        return _probe_direct(settings, normalized, model_name)
    raise ProviderError(f"Provedor desconhecido: {normalized}.")


def preflight_all(
    settings: Settings | None = None,
    *,
    quick: bool = True,
) -> list[ProviderPreflight]:
    """Lista provedores sem aquecer modelos locais por padrão.

    A interface, o menu e o painel de saúde usam a validação rápida. Toda
    investigação repete o preflight completo antes de abrir qualquer SSH.
    """
    settings = settings or get_settings()
    providers = ("gemini", "groq", "openrouter", "ollama", "omniroute")
    with ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="ai-preflight") as pool:
        return list(
            pool.map(
                lambda provider: preflight_provider(
                    provider,
                    settings,
                    quick=quick,
                ),
                providers,
            )
        )


def selected_provider_preflight(settings: Settings | None = None) -> ProviderPreflight:
    settings = settings or get_settings()
    provider = (current_provider_override() or settings.ai_provider or "gemini").strip().lower()
    model = current_model_override()
    return preflight_provider(provider, settings, model, quick=False)


def require_selected_provider(settings: Settings | None = None) -> ProviderPreflight:
    result = selected_provider_preflight(settings)
    if not result.selectable:
        raise ProviderError(
            f"{result.label} indisponível antes da investigação: {result.detail} "
            "Execute 'agent doctor ai' para o diagnóstico completo."
        )
    return result
