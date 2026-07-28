from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field

from app import web as web_module
from app.core.settings import Settings, get_settings
from app.services.ai_providers import omniroute_route_options
from app.services.provider_preflight import ProviderPreflight, preflight_all, preflight_provider
from app.services.provider_registry import (
    builtin_env_updates,
    delete_custom_provider,
    provider_spec,
    public_registry,
    save_custom_provider,
    update_env_values,
)
from app.web import InvestigationPayload, _require_access, _require_mutation


router = APIRouter(tags=["interface-settings"])
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_BUILTIN_IDS = {"gemini", "groq", "deepseek", "openrouter", "ollama", "omniroute"}


class ProviderConfigurationPayload(BaseModel):
    id: str = Field(min_length=2, max_length=48)
    label: str = Field(default="", max_length=80)
    api_key: str | None = Field(default=None, max_length=12000)
    base_url: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, max_length=255)
    models: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    tier: str = Field(default="custom", max_length=20)
    priority: int = Field(default=100, ge=1, le=999)


class ProviderOrderPayload(BaseModel):
    providers: list[str] = Field(min_length=1, max_length=100)


def _validate_registered_provider(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized == "auto":
        return normalized
    if not _PROVIDER_ID.fullmatch(normalized):
        raise ValueError("identificador de provedor inválido")
    spec = provider_spec(normalized, get_settings())
    if not spec or not spec.enabled:
        raise ValueError(f"provedor não cadastrado ou desabilitado: {normalized}")
    return normalized


def _dynamic_provider_options(result: ProviderPreflight, settings: Settings) -> list[dict[str, Any]]:
    if result.provider == "omniroute":
        configured = omniroute_route_options(settings)
        valid = set(result.valid_routes)
        rows = [
            {
                "value": route.model,
                "label": route.label,
                "default": route.is_default,
                "available": not valid or route.model in valid,
            }
            for route in configured
        ]
        if result.model and result.model not in {item["value"] for item in rows}:
            rows.insert(
                0,
                {
                    "value": result.model,
                    "label": result.model,
                    "default": True,
                    "available": result.selectable,
                },
            )
        return rows

    spec = provider_spec(result.provider, settings)
    models = list(spec.models if spec else ())
    if result.model and result.model not in models:
        models.insert(0, result.model)
    valid = set(result.valid_routes)
    return [
        {
            "value": model,
            "label": model,
            "default": model == result.model or bool(spec and model == spec.default_model),
            "available": not valid or model in valid,
        }
        for model in models
        if model
    ]


def enable_dynamic_provider_payload() -> None:
    """Permite IDs cadastrados, mantendo rejeição Pydantic para IDs desconhecidos."""
    dynamic_type = Annotated[str | None, AfterValidator(_validate_registered_provider)]
    InvestigationPayload.__annotations__["provider"] = dynamic_type
    InvestigationPayload.model_fields["provider"].annotation = dynamic_type
    InvestigationPayload.model_fields["provider"].metadata = []
    InvestigationPayload.model_rebuild(force=True)
    web_module._provider_options = _dynamic_provider_options


def _fresh_settings() -> Settings:
    get_settings.cache_clear()
    return Settings()


def _settings_enabled(settings: Settings) -> None:
    if not settings.ai_settings_ui_enabled:
        raise HTTPException(status_code=404, detail="configuração de IA desabilitada")


def _public_diagnostics(settings: Settings) -> dict[str, dict[str, Any]]:
    try:
        diagnostics = preflight_all(settings, quick=True)
    except Exception:
        return {}
    return {
        item.provider: {
            "state": item.state.value,
            "state_label": item.state_label,
            "model": item.model,
            "detail": item.detail,
            "latency_ms": item.latency_ms,
            "selectable": item.selectable,
            "valid_routes": list(item.valid_routes),
        }
        for item in diagnostics
    }


@router.get("/ui/api/settings/ai")
def ai_settings(request: Request) -> dict[str, Any]:
    _require_access(request)
    settings = _fresh_settings()
    _settings_enabled(settings)
    registry = public_registry(settings)
    diagnostics = _public_diagnostics(settings)
    providers = [
        {**item, "diagnostic": diagnostics.get(item["id"])}
        for item in registry["providers"]
    ]
    return {
        "enabled": True,
        "allow_secret_write": settings.ai_settings_allow_secret_write,
        "automatic_order": [
            item.strip()
            for item in settings.ai_auto_provider_order.split(",")
            if item.strip()
        ],
        "providers": providers,
        "presets": [
            {
                "id": "deepseek",
                "label": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "default_model": "deepseek-v4-flash",
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                "tier": "paid",
                "builtin": True,
            },
            {
                "id": "openai-compatible",
                "label": "API OpenAI-compatible personalizada",
                "base_url": "https://api.exemplo.com/v1",
                "default_model": "",
                "models": [],
                "tier": "custom",
                "builtin": False,
            },
        ],
        "registry_path": registry["registry_path"],
        "env_path": registry["env_path"],
        "queue_note": (
            "As alterações entram imediatamente na interface. Em modo queue, "
            "reinicie os workers para processos antigos recarregarem o .env."
            if settings.agent_execution_mode.strip().casefold() == "queue"
            else "As alterações entram no próximo diagnóstico ou investigação."
        ),
    }


@router.put("/ui/api/settings/ai/providers/{provider_id}")
def save_provider_configuration(
    provider_id: str,
    payload: ProviderConfigurationPayload,
    request: Request,
) -> dict[str, Any]:
    _require_mutation(request)
    settings = _fresh_settings()
    _settings_enabled(settings)
    normalized = provider_id.strip().lower()
    if normalized != payload.id.strip().lower():
        raise HTTPException(status_code=422, detail="o identificador da rota e do formulário deve ser igual")
    if not _PROVIDER_ID.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="identificador de provedor inválido")
    if payload.api_key and not settings.ai_settings_allow_secret_write:
        raise HTTPException(status_code=403, detail="gravação de chaves está desabilitada")

    try:
        if normalized in _BUILTIN_IDS:
            updates = builtin_env_updates(
                normalized,
                api_key=payload.api_key,
                base_url=payload.base_url,
                default_model=payload.default_model,
                models=payload.models or None,
                settings=settings,
            )
            if not updates:
                raise ValueError("nenhum valor foi informado para atualização")
            update_env_values(updates, settings=settings)
        else:
            save_custom_provider(
                provider_id=normalized,
                label=payload.label,
                base_url=payload.base_url or "",
                default_model=payload.default_model or "",
                models=payload.models,
                api_key=payload.api_key,
                enabled=payload.enabled,
                tier=payload.tier,
                priority=payload.priority,
                settings=settings,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"não foi possível salvar o provedor: {type(exc).__name__}",
        ) from exc

    refreshed = _fresh_settings()
    spec = provider_spec(normalized, refreshed)
    if not spec:
        raise HTTPException(status_code=500, detail="provedor salvo, mas não recarregado")
    return {
        "saved": True,
        "provider": spec.public_dict(refreshed),
        "message": "Configuração salva. A chave foi mantida somente no backend.",
    }


@router.delete("/ui/api/settings/ai/providers/{provider_id}")
def remove_provider_configuration(provider_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = _fresh_settings()
    _settings_enabled(settings)
    normalized = provider_id.strip().lower()
    if normalized in _BUILTIN_IDS:
        raise HTTPException(status_code=409, detail="provedores nativos não podem ser excluídos")
    if not delete_custom_provider(normalized, settings):
        raise HTTPException(status_code=404, detail="provedor personalizado não encontrado")
    return {"deleted": True, "provider": normalized}


@router.put("/ui/api/settings/ai/order")
def save_provider_order(payload: ProviderOrderPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = _fresh_settings()
    _settings_enabled(settings)
    registered = {
        item["id"]
        for item in public_registry(settings)["providers"]
        if item.get("enabled", True)
    }
    ordered: list[str] = []
    for raw in payload.providers:
        item = raw.strip().lower()
        if item not in registered:
            raise HTTPException(status_code=422, detail=f"provedor não cadastrado: {item}")
        if item not in ordered:
            ordered.append(item)
    update_env_values({"AI_AUTO_PROVIDER_ORDER": ",".join(ordered)}, settings=settings)
    return {"saved": True, "providers": ordered}


@router.post("/ui/api/settings/ai/providers/{provider_id}/test")
def test_provider_configuration(provider_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = _fresh_settings()
    _settings_enabled(settings)
    normalized = provider_id.strip().lower()
    if not provider_spec(normalized, settings):
        raise HTTPException(status_code=404, detail="provedor não cadastrado")
    try:
        result = preflight_provider(normalized, settings, quick=False)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"falha ao testar provedor: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        **result.model_dump(mode="json"),
        "state_label": result.state_label,
    }
