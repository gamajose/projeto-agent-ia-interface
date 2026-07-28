from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from dotenv import dotenv_values

from app.core.settings import PROJECT_ROOT, Settings, get_settings
from app.services.secrets import clear_secret_cache, get_secret


_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")
_ALLOWED_TIERS = {"free", "paid", "local", "gateway", "custom"}
_LEGACY_CATALOG_IDS = {"gemini", "groq", "openrouter", "ollama", "omniroute"}


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    kind: str
    source: str
    base_url: str
    default_model: str
    models: tuple[str, ...]
    credential_env: str | None = None
    enabled: bool = True
    tier: str = "custom"
    priority: int = 100
    headers: dict[str, str] = field(default_factory=dict)
    builtin: bool = False

    def public_dict(self, settings: Settings | None = None) -> dict[str, Any]:
        settings = settings or get_settings()
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "source": self.source,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "models": list(self.models),
            "credential_env": self.credential_env,
            "configured": provider_configured(self, settings),
            "enabled": self.enabled,
            "tier": self.tier,
            "priority": self.priority,
            "builtin": self.builtin,
        }


def _split_models(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    rows = value if not isinstance(value, str) else re.split(r"[,\n]", value)
    output: list[str] = []
    for row in rows:
        item = str(row).strip()
        if item and item not in output:
            output.append(item)
    return tuple(output)


def _registry_path(settings: Settings) -> Path:
    return Path(settings.ai_provider_registry_path).expanduser()


def _env_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    configured = str(getattr(settings, "ai_settings_env_path", "") or "").strip()
    return Path(configured).expanduser() if configured else PROJECT_ROOT / ".env"


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _openrouter_headers(settings: Settings) -> dict[str, str]:
    headers = {"X-Title": settings.openrouter_app_name}
    if settings.openrouter_site_url:
        headers["HTTP-Referer"] = settings.openrouter_site_url
    return headers


def _builtin_specs(settings: Settings) -> tuple[ProviderSpec, ...]:
    return (
        ProviderSpec(
            id="gemini",
            label="Google Gemini",
            kind="gemini",
            source="direct",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            default_model=settings.gemini_model,
            models=_split_models(settings.gemini_free_models),
            credential_env="GEMINI_API_KEY",
            tier="free",
            priority=30,
            builtin=True,
        ),
        ProviderSpec(
            id="groq",
            label="Groq (Llama)",
            kind="openai-compatible",
            source="direct",
            base_url=settings.groq_base_url,
            default_model=settings.groq_model,
            models=(settings.groq_model,),
            credential_env="GROQ_API_KEY",
            tier="free",
            priority=10,
            builtin=True,
        ),
        ProviderSpec(
            id="deepseek",
            label="DeepSeek",
            kind="openai-compatible",
            source="direct",
            base_url=settings.deepseek_base_url,
            default_model=settings.deepseek_model,
            models=_split_models(settings.deepseek_models),
            credential_env="DEEPSEEK_API_KEY",
            tier="paid",
            priority=25,
            builtin=True,
        ),
        ProviderSpec(
            id="openrouter",
            label="OpenRouter",
            kind="openai-compatible",
            source="direct",
            base_url=settings.openrouter_base_url,
            default_model=settings.openrouter_model,
            models=(settings.openrouter_model,),
            credential_env="OPENROUTER_API_KEY",
            tier="free",
            priority=60,
            headers=_openrouter_headers(settings),
            builtin=True,
        ),
        ProviderSpec(
            id="ollama",
            label="Ollama local",
            kind="ollama",
            source="local",
            base_url=settings.ollama_base_url,
            default_model=settings.ollama_model,
            models=_split_models(settings.ollama_preferred_models),
            credential_env=None,
            tier="local",
            priority=50,
            builtin=True,
        ),
        ProviderSpec(
            id="omniroute",
            label="OmniRoute",
            kind="gateway",
            source="gateway",
            base_url=settings.omniroute_base_url,
            default_model=(settings.omniroute_default_route or settings.omniroute_model or "").strip(),
            models=tuple(
                item.strip().split("=", 1)[-1].strip()
                for item in re.split(r"[,\n]", settings.omniroute_routes or "")
                if item.strip()
            ),
            credential_env="OMNIROUTE_API_KEY",
            tier="gateway",
            priority=20,
            builtin=True,
        ),
    )


def _load_custom_rows(settings: Settings) -> list[dict[str, Any]]:
    path = _registry_path(settings)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("providers", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _custom_spec(row: dict[str, Any]) -> ProviderSpec | None:
    provider_id = str(row.get("id") or "").strip().lower()
    if not _PROVIDER_ID.fullmatch(provider_id):
        return None
    credential_env = str(row.get("credential_env") or "").strip().upper()
    if credential_env and not _ENV_NAME.fullmatch(credential_env):
        return None
    base_url = str(row.get("base_url") or "").strip()
    if not _valid_http_url(base_url):
        return None
    models = _split_models(row.get("models"))
    default_model = str(row.get("default_model") or "").strip()
    if default_model and default_model not in models:
        models = (default_model, *models)
    tier = str(row.get("tier") or "custom").strip().lower()
    return ProviderSpec(
        id=provider_id,
        label=str(row.get("label") or provider_id).strip()[:80] or provider_id,
        kind="openai-compatible",
        source="direct",
        base_url=base_url,
        default_model=default_model,
        models=models,
        credential_env=credential_env or f"AI_PROVIDER_{provider_id.upper().replace('-', '_')}_API_KEY",
        enabled=bool(row.get("enabled", True)),
        tier=tier if tier in _ALLOWED_TIERS else "custom",
        priority=max(1, min(int(row.get("priority", 100)), 999)),
        headers={
            str(key): str(value)
            for key, value in dict(row.get("headers") or {}).items()
            if str(key).strip() and str(value).strip()
        },
        builtin=False,
    )


def provider_specs(
    settings: Settings | None = None,
    *,
    include_disabled: bool = False,
) -> tuple[ProviderSpec, ...]:
    settings = settings or get_settings()
    specs = list(_builtin_specs(settings))
    builtin_ids = {item.id for item in specs}
    for row in _load_custom_rows(settings):
        spec = _custom_spec(row)
        if spec and spec.id not in builtin_ids:
            specs.append(spec)
    if not include_disabled:
        specs = [item for item in specs if item.enabled]
    return tuple(sorted(specs, key=lambda item: (item.priority, item.label.casefold())))


def provider_spec(provider_id: str, settings: Settings | None = None) -> ProviderSpec | None:
    normalized = str(provider_id or "").strip().lower()
    return next(
        (item for item in provider_specs(settings, include_disabled=True) if item.id == normalized),
        None,
    )


def provider_label(provider_id: str, settings: Settings | None = None) -> str:
    spec = provider_spec(provider_id, settings)
    return spec.label if spec else str(provider_id or "").strip().replace("_", " ").title()


def _dotenv_value(name: str, settings: Settings) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    path = _env_path(settings)
    if not path.is_file():
        return None
    loaded = dotenv_values(path).get(name)
    return str(loaded).strip() if loaded else None


def provider_secret(spec: ProviderSpec, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if not spec.credential_env:
        return None
    attribute_map = {
        "GEMINI_API_KEY": "gemini_api_key",
        "GROQ_API_KEY": "groq_api_key",
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "OPENROUTER_API_KEY": "openrouter_api_key",
        "OMNIROUTE_API_KEY": "omniroute_api_key",
    }
    attribute = attribute_map.get(spec.credential_env)
    fallback = getattr(settings, attribute, None) if attribute else None
    fallback = fallback or _dotenv_value(spec.credential_env, settings)
    return get_secret(spec.credential_env, fallback, settings=settings)


def provider_configured(spec: ProviderSpec, settings: Settings | None = None) -> bool:
    if spec.kind == "ollama":
        return True
    try:
        return bool(provider_secret(spec, settings))
    except Exception:
        return False


def provider_ids(settings: Settings | None = None) -> tuple[str, ...]:
    """Retorna o catálogo operacional.

    Os cinco provedores históricos continuam visíveis mesmo sem chave. DeepSeek e
    provedores personalizados entram automaticamente após receberem credencial,
    evitando aumentar o catálogo com integrações ainda não configuradas.
    """
    settings = settings or get_settings()
    rows = []
    for spec in provider_specs(settings):
        if spec.id in _LEGACY_CATALOG_IDS or provider_configured(spec, settings):
            rows.append(spec.id)
    return tuple(rows)


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        temporary.replace(path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)


def update_env_values(
    updates: dict[str, str],
    *,
    settings: Settings | None = None,
) -> Path:
    settings = settings or get_settings()
    path = _env_path(settings)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    positions: dict[str, int] = {}
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        positions[stripped.split("=", 1)[0].strip()] = index

    for key, value in updates.items():
        normalized_key = str(key).strip().upper()
        normalized_value = str(value).strip()
        if not _ENV_NAME.fullmatch(normalized_key):
            raise ValueError(f"nome de variável inválido: {normalized_key}")
        if any(char in normalized_value for char in ("\n", "\r", "\x00")):
            raise ValueError(f"valor inválido para {normalized_key}")
        row = f"{normalized_key}={normalized_value}"
        if normalized_key in positions:
            lines[positions[normalized_key]] = row
        else:
            lines.append(row)

    _atomic_write(path, "\n".join(lines).rstrip() + "\n")
    get_settings.cache_clear()
    clear_secret_cache()
    return path


def _save_custom_rows(rows: list[dict[str, Any]], settings: Settings) -> Path:
    path = _registry_path(settings)
    _atomic_write(
        path,
        json.dumps({"version": 1, "providers": rows}, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def save_custom_provider(
    *,
    provider_id: str,
    label: str,
    base_url: str,
    default_model: str,
    models: Iterable[str],
    api_key: str | None,
    enabled: bool,
    tier: str,
    priority: int,
    settings: Settings | None = None,
) -> ProviderSpec:
    settings = settings or get_settings()
    normalized_id = provider_id.strip().lower()
    if not _PROVIDER_ID.fullmatch(normalized_id):
        raise ValueError("identificador deve começar com letra e usar apenas letras, números, hífen ou underscore")
    if normalized_id in {item.id for item in _builtin_specs(settings)}:
        raise ValueError("use a configuração do provedor nativo para esse identificador")
    if not _valid_http_url(base_url):
        raise ValueError("base URL inválida")
    normalized_models = _split_models(models)
    normalized_default = default_model.strip()
    if not normalized_default:
        raise ValueError("modelo padrão é obrigatório")
    if normalized_default not in normalized_models:
        normalized_models = (normalized_default, *normalized_models)
    env_name = f"AI_PROVIDER_{normalized_id.upper().replace('-', '_')}_API_KEY"
    row = {
        "id": normalized_id,
        "label": label.strip()[:80] or normalized_id,
        "kind": "openai-compatible",
        "base_url": base_url.rstrip("/"),
        "default_model": normalized_default,
        "models": list(normalized_models),
        "credential_env": env_name,
        "enabled": bool(enabled),
        "tier": tier if tier in _ALLOWED_TIERS else "custom",
        "priority": max(1, min(int(priority), 999)),
    }
    rows = [
        item
        for item in _load_custom_rows(settings)
        if str(item.get("id") or "").strip().lower() != normalized_id
    ]
    rows.append(row)
    _save_custom_rows(rows, settings)
    if api_key:
        update_env_values({env_name: api_key}, settings=settings)
    get_settings.cache_clear()
    spec = provider_spec(normalized_id, settings)
    if not spec:
        raise RuntimeError("provedor foi salvo, mas não pôde ser recarregado")
    return spec


def delete_custom_provider(provider_id: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    normalized = provider_id.strip().lower()
    rows = _load_custom_rows(settings)
    remaining = [
        item
        for item in rows
        if str(item.get("id") or "").strip().lower() != normalized
    ]
    if len(remaining) == len(rows):
        return False
    _save_custom_rows(remaining, settings)
    get_settings.cache_clear()
    return True


def builtin_env_updates(
    provider_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    models: Iterable[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, str]:
    settings = settings or get_settings()
    normalized = provider_id.strip().lower()
    mapping: dict[str, dict[str, str]] = {
        "gemini": {"key": "GEMINI_API_KEY", "model": "GEMINI_MODEL", "models": "GEMINI_FREE_MODELS"},
        "groq": {"key": "GROQ_API_KEY", "base": "GROQ_BASE_URL", "model": "GROQ_MODEL"},
        "deepseek": {"key": "DEEPSEEK_API_KEY", "base": "DEEPSEEK_BASE_URL", "model": "DEEPSEEK_MODEL", "models": "DEEPSEEK_MODELS"},
        "openrouter": {"key": "OPENROUTER_API_KEY", "base": "OPENROUTER_BASE_URL", "model": "OPENROUTER_MODEL"},
        "ollama": {"base": "OLLAMA_BASE_URL", "model": "OLLAMA_MODEL", "models": "OLLAMA_PREFERRED_MODELS"},
        "omniroute": {"key": "OMNIROUTE_API_KEY", "base": "OMNIROUTE_BASE_URL", "model": "OMNIROUTE_DEFAULT_ROUTE", "models": "OMNIROUTE_ROUTES"},
    }
    if normalized not in mapping:
        raise ValueError("provedor nativo desconhecido")
    fields = mapping[normalized]
    updates: dict[str, str] = {}
    if api_key and fields.get("key"):
        updates[fields["key"]] = api_key
    if base_url is not None and fields.get("base"):
        if not _valid_http_url(base_url):
            raise ValueError("base URL inválida")
        updates[fields["base"]] = base_url.rstrip("/")
    if default_model is not None and fields.get("model"):
        updates[fields["model"]] = default_model.strip()
    if models is not None and fields.get("models"):
        updates[fields["models"]] = ",".join(_split_models(models))
    return updates


def public_registry(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "registry_path": str(_registry_path(settings)),
        "env_path": str(_env_path(settings)),
        "providers": [
            item.public_dict(settings)
            for item in provider_specs(settings, include_disabled=True)
        ],
    }
