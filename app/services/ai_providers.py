from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Protocol

import httpx
from google import genai
from google.genai import types

from app.core.settings import Settings, get_settings
from app.services.provider_registry import (
    ProviderSpec,
    provider_configured,
    provider_ids,
    provider_label,
    provider_secret,
    provider_spec,
    provider_specs,
)
from app.services.secrets import get_secret


class ProviderError(RuntimeError):
    pass


class AIProvider(Protocol):
    name: str
    model: str

    def generate_json(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]: ...


def parse_json(text: str) -> dict[str, Any]:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.S)
        if not match:
            raise
        result = json.loads(match.group(0))
    if not isinstance(result, dict):
        raise ValueError("A resposta da IA não é um objeto JSON.")
    return result


@dataclass
class GeminiProvider:
    api_key: str
    model: str
    name: str = "gemini"

    def generate_json(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        response = genai.Client(api_key=self.api_key).models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
        )
        text = response.text or ""
        return parse_json(text), {"response_chars": len(text)}


@dataclass
class OpenAICompatibleProvider:
    name: str
    api_key: str
    model: str
    base_url: str
    headers: dict[str, str] | None = None

    def generate_json(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", **(self.headers or {})},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            timeout=90,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"] or ""
        return parse_json(text), {"response_chars": len(text), "status_code": response.status_code}


@dataclass
class OllamaProvider:
    model: str
    base_url: str
    name: str = "ollama"

    def generate_json(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=180,
        )
        response.raise_for_status()
        text = response.json().get("response") or ""
        return parse_json(text), {"response_chars": len(text), "status_code": response.status_code}


@dataclass(frozen=True)
class GatewayRoute:
    label: str
    model: str
    is_default: bool = False


PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "groq": "Groq (Llama)",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
    "ollama": "Ollama local",
    "omniroute": "OmniRoute",
}

_PROVIDER_OVERRIDE: ContextVar[str | None] = ContextVar("agent_ai_provider_override", default=None)
_MODEL_OVERRIDE: ContextVar[str | None] = ContextVar("agent_ai_model_override", default=None)


@contextmanager
def use_provider(name: str | None, model: str | None = None) -> Iterator[None]:
    provider_token = _PROVIDER_OVERRIDE.set((name or "").strip().lower() or None)
    model_token = _MODEL_OVERRIDE.set((model or "").strip() or None)
    try:
        yield
    finally:
        _MODEL_OVERRIDE.reset(model_token)
        _PROVIDER_OVERRIDE.reset(provider_token)


def current_provider_override() -> str | None:
    return _PROVIDER_OVERRIDE.get()


def current_model_override() -> str | None:
    return _MODEL_OVERRIDE.get()


def _secret(settings: Settings, name: str, attribute: str) -> str | None:
    fallback = getattr(settings, attribute, None)
    try:
        return get_secret(name, fallback, settings=settings)
    except AttributeError:
        return fallback


def _uses_dynamic_registry(settings: Any) -> bool:
    return hasattr(settings, "ai_provider_registry_path")


def _legacy_direct_status(settings: Any) -> list[dict[str, Any]]:
    rows = (
        ("gemini", "Google Gemini", getattr(settings, "gemini_model", ""), getattr(settings, "gemini_api_key", None)),
        ("groq", "Groq (Llama)", getattr(settings, "groq_model", ""), getattr(settings, "groq_api_key", None)),
        ("openrouter", "OpenRouter", getattr(settings, "openrouter_model", ""), getattr(settings, "openrouter_api_key", None)),
    )
    return [
        {
            "kind": "provider",
            "source": "direct",
            "name": name,
            "label": label,
            "model": model,
            "configured": bool(key),
            "tier": "free",
            "builtin": True,
        }
        for name, label, model, key in rows
    ]


def direct_provider_status(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    if not _uses_dynamic_registry(settings):
        return _legacy_direct_status(settings)

    active_ids = set(provider_ids(settings))
    display_rank = {"gemini": 0, "groq": 1, "deepseek": 2, "openrouter": 3}
    specs = sorted(
        provider_specs(settings),
        key=lambda spec: (display_rank.get(spec.id, 100), spec.priority, spec.label.casefold()),
    )
    rows: list[dict[str, Any]] = []
    for spec in specs:
        if spec.source != "direct" or spec.id not in active_ids:
            continue
        rows.append(
            {
                "kind": "provider",
                "source": spec.source,
                "name": spec.id,
                "label": spec.label,
                "model": spec.default_model,
                "configured": provider_configured(spec, settings),
                "tier": spec.tier,
                "builtin": spec.builtin,
            }
        )
    return rows


def local_provider_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if not _uses_dynamic_registry(settings):
        return {
            "kind": "provider",
            "source": "local",
            "name": "ollama",
            "label": "Ollama local",
            "model": getattr(settings, "ollama_model", ""),
            "configured": True,
            "tier": "local",
            "builtin": True,
        }
    spec = provider_spec("ollama", settings)
    return {
        "kind": "provider",
        "source": "local",
        "name": "ollama",
        "label": provider_label("ollama", settings),
        "model": spec.default_model if spec else settings.ollama_model,
        "configured": True,
        "tier": "local",
        "builtin": True,
    }


def provider_status(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    return [*direct_provider_status(settings), local_provider_status(settings)]


def _default_omniroute_route(settings: Settings) -> str:
    return (
        getattr(settings, "omniroute_default_route", "")
        or getattr(settings, "omniroute_model", "")
        or ""
    ).strip()


def omniroute_route_options(settings: Settings | None = None) -> list[GatewayRoute]:
    settings = settings or get_settings()
    default_route = _default_omniroute_route(settings)
    routes: list[GatewayRoute] = []
    seen: set[str] = set()
    for raw_item in re.split(r"[,\n]", getattr(settings, "omniroute_routes", "") or ""):
        item = raw_item.strip()
        if not item:
            continue
        if "=" in item:
            label, model = (part.strip() for part in item.split("=", 1))
        elif "|" in item:
            label, model = (part.strip() for part in item.split("|", 1))
        else:
            label = model = item
        if not model or model in seen:
            continue
        seen.add(model)
        routes.append(GatewayRoute(label=label or model, model=model, is_default=model == default_route))
    if default_route and default_route not in seen:
        routes.insert(0, GatewayRoute(label=default_route, model=default_route, is_default=True))
    return routes


def gateway_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if not _uses_dynamic_registry(settings):
        configured = bool(getattr(settings, "omniroute_api_key", None))
    else:
        spec = provider_spec("omniroute", settings)
        configured = bool(spec and provider_configured(spec, settings))
    return {
        "kind": "gateway",
        "source": "gateway",
        "name": "omniroute",
        "label": "OmniRoute — gateway centralizado",
        "configured": configured,
        "base_url": getattr(settings, "omniroute_base_url", ""),
        "default_route": _default_omniroute_route(settings),
        "routes": omniroute_route_options(settings),
    }


def _headers_for_spec(spec: ProviderSpec, settings: Settings) -> dict[str, str]:
    headers = dict(spec.headers)
    if spec.id == "openrouter" and getattr(settings, "openrouter_site_url", None):
        headers["HTTP-Referer"] = settings.openrouter_site_url
    return headers


def _legacy_get_provider(selected: str, selected_model: str, settings: Any) -> AIProvider:
    if selected == "gemini":
        key = getattr(settings, "gemini_api_key", None)
        if not key:
            raise ProviderError("GEMINI_API_KEY não configurada.")
        return GeminiProvider(key, selected_model or getattr(settings, "gemini_model", ""))
    if selected == "groq":
        key = getattr(settings, "groq_api_key", None)
        if not key:
            raise ProviderError("GROQ_API_KEY não configurada.")
        return OpenAICompatibleProvider(
            "groq", key, selected_model or getattr(settings, "groq_model", ""), getattr(settings, "groq_base_url", "")
        )
    if selected == "openrouter":
        key = getattr(settings, "openrouter_api_key", None)
        if not key:
            raise ProviderError("OPENROUTER_API_KEY não configurada.")
        headers = {"X-Title": getattr(settings, "openrouter_app_name", "Agent IA Infra")}
        if getattr(settings, "openrouter_site_url", None):
            headers["HTTP-Referer"] = settings.openrouter_site_url
        return OpenAICompatibleProvider(
            "openrouter", key, selected_model or getattr(settings, "openrouter_model", ""), getattr(settings, "openrouter_base_url", ""), headers
        )
    if selected == "ollama":
        return OllamaProvider(selected_model or getattr(settings, "ollama_model", ""), getattr(settings, "ollama_base_url", ""))
    if selected == "omniroute":
        key = getattr(settings, "omniroute_api_key", None)
        if not key:
            raise ProviderError("OMNIROUTE_API_KEY não configurada.")
        model = selected_model or _default_omniroute_route(settings)
        if not model:
            raise ProviderError("Selecione uma rota/modelo do OmniRoute.")
        return OpenAICompatibleProvider("omniroute", key, model, getattr(settings, "omniroute_base_url", ""))
    raise ProviderError(f"Provedor desconhecido: {selected}.")


def get_provider(
    name: str | None = None,
    settings: Settings | None = None,
    model_name: str | None = None,
) -> AIProvider:
    settings = settings or get_settings()
    selected = (name or current_provider_override() or getattr(settings, "ai_provider", "gemini") or "gemini").strip().lower()
    selected_model = (model_name or current_model_override() or "").strip()

    if not _uses_dynamic_registry(settings):
        return _legacy_get_provider(selected, selected_model, settings)

    spec = provider_spec(selected, settings)
    if not spec or not spec.enabled:
        raise ProviderError(f"Provedor desconhecido ou desabilitado: {selected}.")
    if spec.kind == "gemini":
        api_key = provider_secret(spec, settings)
        if not api_key:
            raise ProviderError("GEMINI_API_KEY não configurada.")
        return GeminiProvider(api_key, selected_model or spec.default_model)
    if spec.kind == "ollama":
        return OllamaProvider(selected_model or spec.default_model, spec.base_url)
    if spec.kind in {"openai-compatible", "gateway"}:
        api_key = provider_secret(spec, settings)
        if not api_key:
            raise ProviderError(f"{spec.credential_env or selected.upper() + '_API_KEY'} não configurada.")
        model = selected_model or spec.default_model
        if not model:
            if spec.id == "omniroute":
                raise ProviderError("Selecione uma rota/modelo do OmniRoute no menu ou configure OMNIROUTE_DEFAULT_ROUTE.")
            raise ProviderError(f"Modelo padrão não configurado para {spec.label}.")
        return OpenAICompatibleProvider(spec.id, api_key, model, spec.base_url, _headers_for_spec(spec, settings))
    raise ProviderError(f"Tipo de provedor não suportado: {spec.kind}.")
