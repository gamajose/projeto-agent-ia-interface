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
    ProviderError,
    _default_omniroute_route,
    current_model_override,
    current_provider_override,
    omniroute_route_options,
    parse_json,
)
from app.services.provider_registry import (
    ProviderSpec,
    provider_ids,
    provider_label,
    provider_secret,
    provider_spec,
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
    settings: Settings | None = None,
) -> ProviderPreflight:
    return ProviderPreflight(
        provider=provider,
        label=provider_label(provider, settings),
        state=state,
        model=model,
        detail=detail,
        latency_ms=_elapsed_ms(started) if started is not None else None,
        selectable=state == ProviderState.AVAILABLE if selectable is None else selectable,
        valid_routes=valid_routes,
        invalid_routes=invalid_routes,
    )


def _safe_http_message(response: httpx.Response) -> str:
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
    return " ".join(message.split())[:300]


def _http_failure(
    provider: str,
    model: str,
    exc: Exception,
    started: float,
    settings: Settings | None = None,
) -> ProviderPreflight:
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
                settings=settings,
            )
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail=f"O serviço respondeu com erro HTTP {status}.{suffix}",
            started=started,
            settings=settings,
        )
    if isinstance(exc, httpx.TimeoutException):
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail="Tempo limite excedido ao consultar o serviço.",
            started=started,
            settings=settings,
        )
    if isinstance(exc, httpx.RequestError):
        return _result(
            provider,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail="Não foi possível conectar ao endpoint configurado.",
            started=started,
            settings=settings,
        )
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return _result(
            provider,
            state=ProviderState.DEGRADED,
            model=model,
            detail="O serviço respondeu, mas não retornou JSON válido no formato esperado.",
            started=started,
            settings=settings,
        )
    return _result(
        provider,
        state=ProviderState.UNAVAILABLE,
        model=model,
        detail=f"Falha inesperada durante o diagnóstico ({type(exc).__name__}).",
        started=started,
        settings=settings,
    )


def _ollama_models(settings: Settings) -> tuple[str, ...]:
    response = httpx.get(
        f"{settings.ollama_base_url.rstrip('/')}/api/tags",
        timeout=_timeout(settings),
    )
    response.raise_for_status()
    payload = response.json()
    models = {
        str(value).strip()
        for item in payload.get("models", [])
        if isinstance(item, dict)
        for value in (item.get("name"), item.get("model"))
        if value
    }
    return tuple(sorted(item for item in models if item))


def _select_ollama_model(
    settings: Settings,
    installed: tuple[str, ...],
    model_name: str | None,
) -> tuple[str, str | None]:
    requested = (model_name or "").strip()
    configured = (settings.ollama_model or "").strip()
    if model_name is not None and requested:
        return requested, None
    if configured and configured.casefold() not in _AUTO_MODEL_VALUES and configured in installed:
        return configured, None
    if not settings.ollama_auto_fallback:
        return configured, None
    preferred = _csv_values(settings.ollama_preferred_models)
    selected = next((item for item in preferred if item in installed), installed[0] if installed else "")
    fallback = configured if configured and configured != selected else None
    return selected, fallback


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
                settings=settings,
            )
        model, fallback = _select_ollama_model(settings, installed, model_name)
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
                settings=settings,
            )
        if quick:
            if fallback:
                return _result(
                    "ollama",
                    state=ProviderState.DEGRADED,
                    model=model,
                    detail=(
                        f"O modelo configurado '{fallback}' não está instalado; "
                        f"o modelo local '{model}' foi detectado e será validado ao iniciar a investigação."
                    ),
                    started=started,
                    selectable=True,
                    valid_routes=installed,
                    invalid_routes=(fallback,),
                    settings=settings,
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
                settings=settings,
            )
        try:
            response = httpx.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json={"model": model, "prompt": _PROBE_PROMPT, "stream": False, "format": "json"},
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
                settings=settings,
            )
        parsed = parse_json(str(response.json().get("response") or ""))
        if parsed.get("preflight") is not True:
            raise ValueError("resposta de preflight sem confirmação")
        if fallback:
            return _result(
                "ollama",
                state=ProviderState.DEGRADED,
                model=model,
                detail=(
                    f"O modelo configurado '{fallback}' não está instalado; "
                    f"o Agent selecionou automaticamente '{model}'. API e resposta JSON validadas."
                ),
                started=started,
                selectable=True,
                valid_routes=installed,
                invalid_routes=(fallback,),
                settings=settings,
            )
        return _result(
            "ollama",
            state=ProviderState.AVAILABLE,
            model=model,
            detail="API, modelo e resposta JSON validados.",
            started=started,
            valid_routes=installed,
            settings=settings,
        )
    except Exception as exc:
        return _http_failure("ollama", configured, exc, started, settings)


def _configured_omniroute_routes(settings: Settings) -> tuple[str, ...]:
    return tuple(route.model for route in omniroute_route_options(settings))


def _probe_omniroute(settings: Settings, model_name: str | None = None) -> ProviderPreflight:
    spec = provider_spec("omniroute", settings)
    selected_model = (model_name or _default_omniroute_route(settings)).strip()
    configured_routes = _configured_omniroute_routes(settings)
    try:
        token = provider_secret(spec, settings) if spec else None
    except Exception:
        return _result(
            "omniroute",
            state=ProviderState.UNAVAILABLE,
            model=selected_model,
            detail="Não foi possível consultar o backend de segredos do OmniRoute.",
            settings=settings,
        )
    if not token:
        return _result(
            "omniroute",
            state=ProviderState.NOT_CONFIGURED,
            model=selected_model,
            detail="Falta o token local do endpoint (OMNIROUTE_API_KEY).",
            settings=settings,
        )
    if not selected_model and not configured_routes:
        return _result(
            "omniroute",
            state=ProviderState.MISCONFIGURED,
            model="",
            detail="Configure OMNIROUTE_DEFAULT_ROUTE ou ao menos uma rota em OMNIROUTE_ROUTES.",
            settings=settings,
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
        available = {
            str(item.get("id") or "").strip()
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
        candidates = configured_routes
        if selected_model and selected_model not in candidates:
            candidates = (selected_model, *candidates)
        valid = tuple(item for item in candidates if item in available)
        invalid = tuple(item for item in candidates if item not in available)
        if selected_model and selected_model not in available:
            return _result(
                "omniroute",
                state=ProviderState.MISCONFIGURED,
                model=selected_model,
                detail=f"A rota/modelo '{selected_model}' não existe no gateway.",
                started=started,
                valid_routes=valid,
                invalid_routes=invalid,
                settings=settings,
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
                settings=settings,
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
                settings=settings,
            )
        return _result(
            "omniroute",
            state=ProviderState.AVAILABLE,
            model=selected_model or valid[0],
            detail="Token, endpoint e rotas configuradas foram validados.",
            started=started,
            valid_routes=valid,
            settings=settings,
        )
    except Exception as exc:
        return _http_failure("omniroute", selected_model, exc, started, settings)


def _normalize_gemini_model(value: str | None) -> str:
    return (value or "").strip().removeprefix("models/")


def _gemini_free_candidates(settings: Settings) -> tuple[str, ...]:
    return tuple(_normalize_gemini_model(item) for item in _csv_values(settings.gemini_free_models))


def _gemini_models(api_key: str, settings: Settings) -> tuple[str, ...]:
    output: list[str] = []
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
            if name and name not in output:
                output.append(name)
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break
    return tuple(output)


def _select_gemini_model(
    settings: Settings,
    available: tuple[str, ...],
    model_name: str | None,
) -> tuple[str, str | None, tuple[str, ...], tuple[str, ...]]:
    requested = _normalize_gemini_model(model_name)
    configured = _normalize_gemini_model(settings.gemini_model)
    candidates = _gemini_free_candidates(settings)
    valid = tuple(item for item in candidates if item in available)
    invalid = tuple(item for item in candidates if item not in available)
    if model_name is not None and requested:
        return requested, None, valid, invalid
    if settings.gemini_auto_free:
        selected = valid[0] if valid else configured
        fallback = configured if configured and configured != selected else None
        return selected, fallback, valid, invalid
    return configured, None, valid, invalid


def _gemini_probe_candidates(
    settings: Settings,
    selected: str,
    valid_free: tuple[str, ...],
    model_name: str | None,
) -> tuple[str, ...]:
    ordered = [selected] if selected else []
    if settings.gemini_transient_fallback:
        ordered.extend(item for item in valid_free if item and item not in ordered)
    if model_name is not None and not settings.gemini_transient_fallback:
        return tuple(ordered[:1])
    return tuple(ordered)


def _probe_gemini(
    settings: Settings,
    model_name: str | None = None,
    *,
    quick: bool = False,
) -> ProviderPreflight:
    spec = provider_spec("gemini", settings)
    configured = _normalize_gemini_model(model_name or settings.gemini_model)
    try:
        api_key = provider_secret(spec, settings) if spec else None
    except Exception:
        return _result(
            "gemini",
            state=ProviderState.UNAVAILABLE,
            model=configured,
            detail="Não foi possível consultar o backend de segredos do Gemini.",
            settings=settings,
        )
    if not api_key:
        return _result(
            "gemini",
            state=ProviderState.NOT_CONFIGURED,
            model=configured,
            detail="Falta a credencial GEMINI_API_KEY.",
            settings=settings,
        )
    started = time.monotonic()
    try:
        available = _gemini_models(api_key, settings)
        model, fallback, valid_free, invalid_free = _select_gemini_model(settings, available, model_name)
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
                settings=settings,
            )
        if quick:
            if fallback:
                detail = (
                    f"O modelo configurado '{fallback}' não aparece para esta chave; "
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
                settings=settings,
            )

        failures: list[tuple[str, int, str]] = []
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
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                if parse_json(str(text or "")).get("preflight") is not True:
                    raise ValueError("resposta de preflight sem confirmação")
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in _TRANSIENT_GEMINI_STATUSES and len(candidates) > 1:
                    failures.append((candidate, status, _safe_http_message(exc.response)))
                    continue
                raise

            if failures:
                failed_summary = ", ".join(f"{item} (HTTP {status})" for item, status, _ in failures)
                detail = (
                    f"{failed_summary} apresentou indisponibilidade temporária; "
                    f"fallback automático para o modelo gratuito '{candidate}' validado."
                )
            elif fallback:
                detail = (
                    f"O modelo configurado '{fallback}' não está disponível para esta chave; "
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
                settings=settings,
            )

        failure_summary = ", ".join(f"{item} (HTTP {status})" for item, status, _ in failures)
        public_message = next((message for _, _, message in reversed(failures) if message), "")
        suffix = f" {public_message}" if public_message else ""
        return _result(
            "gemini",
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail=(
                "Todos os modelos gratuitos disponíveis responderam com "
                f"indisponibilidade temporária: {failure_summary}.{suffix}"
            ),
            started=started,
            valid_routes=valid_free,
            invalid_routes=invalid_free,
            settings=settings,
        )
    except Exception as exc:
        return _http_failure("gemini", configured, exc, started, settings)


def _probe_direct(
    settings: Settings,
    spec: ProviderSpec,
    model_name: str | None = None,
) -> ProviderPreflight:
    model = (model_name or spec.default_model or "").strip()
    try:
        api_key = provider_secret(spec, settings)
    except Exception:
        return _result(
            spec.id,
            state=ProviderState.UNAVAILABLE,
            model=model,
            detail="Não foi possível consultar o backend de segredos do provedor.",
            settings=settings,
        )
    if not api_key:
        return _result(
            spec.id,
            state=ProviderState.NOT_CONFIGURED,
            model=model,
            detail=f"Falta a credencial {spec.credential_env or spec.id.upper() + '_API_KEY'}.",
            settings=settings,
        )
    if not model:
        return _result(
            spec.id,
            state=ProviderState.MISCONFIGURED,
            model="",
            detail="O modelo do provedor não está configurado.",
            settings=settings,
        )
    started = time.monotonic()
    try:
        response = httpx.post(
            f"{spec.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", **spec.headers},
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
        text = response.json()["choices"][0]["message"]["content"]
        if parse_json(str(text or "")).get("preflight") is not True:
            raise ValueError("resposta de preflight sem confirmação")
        return _result(
            spec.id,
            state=ProviderState.AVAILABLE,
            model=model,
            detail="Credencial, modelo e resposta JSON validados.",
            started=started,
            valid_routes=spec.models or (model,),
            settings=settings,
        )
    except Exception as exc:
        return _http_failure(spec.id, model, exc, started, settings)


def preflight_provider(
    provider: str,
    settings: Settings | None = None,
    model_name: str | None = None,
    *,
    quick: bool = False,
) -> ProviderPreflight:
    settings = settings or get_settings()
    normalized = provider.strip().lower()
    spec = provider_spec(normalized, settings)
    if not spec or not spec.enabled:
        raise ProviderError(f"Provedor desconhecido ou desabilitado: {normalized}.")
    if spec.kind == "gemini":
        return _probe_gemini(settings, model_name, quick=quick)
    if spec.kind == "ollama":
        return _probe_ollama(settings, model_name, quick=quick)
    if spec.kind == "gateway":
        return _probe_omniroute(settings, model_name)
    if spec.kind == "openai-compatible":
        return _probe_direct(settings, spec, model_name)
    raise ProviderError(f"Tipo de provedor desconhecido: {spec.kind}.")


def preflight_all(
    settings: Settings | None = None,
    *,
    quick: bool = True,
) -> list[ProviderPreflight]:
    """Lista provedores habilitados; modelos locais não são aquecidos no catálogo rápido."""
    settings = settings or get_settings()
    providers = provider_ids(settings)
    with ThreadPoolExecutor(max_workers=max(1, min(len(providers), 12)), thread_name_prefix="ai-preflight") as pool:
        return list(
            pool.map(
                lambda item: preflight_provider(item, settings, quick=quick),
                providers,
            )
        )


def selected_provider_preflight(settings: Settings | None = None) -> ProviderPreflight:
    settings = settings or get_settings()
    provider = (current_provider_override() or settings.ai_provider or "gemini").strip().lower()
    return preflight_provider(provider, settings, current_model_override(), quick=False)


def require_selected_provider(settings: Settings | None = None) -> ProviderPreflight:
    result = selected_provider_preflight(settings)
    if not result.selectable:
        raise ProviderError(
            f"{result.label} indisponível antes da investigação: {result.detail} "
            "Execute 'agent doctor ai' para o diagnóstico completo."
        )
    return result
