from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.settings import Settings
from app.services.ai_providers import OpenAICompatibleProvider, get_provider
from app.services.provider_preflight import ProviderState, preflight_provider
from app.services.provider_registry import (
    public_registry,
    save_custom_provider,
    update_env_values,
)
from app.web import InvestigationPayload
from app.web_settings import enable_dynamic_provider_payload


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": '{"preflight": true}'}}]}


def _settings(tmp_path: Path, **overrides) -> Settings:
    env_path = tmp_path / ".env"
    env_path.write_text("POSTGRES_DSN=sqlite+pysqlite:///:memory:\n", encoding="utf-8")
    values = {
        "postgres_dsn": "sqlite+pysqlite:///:memory:",
        "ai_provider_registry_path": str(tmp_path / "providers.json"),
        "ai_settings_env_path": str(env_path),
        "deepseek_api_key": "deepseek-secret",
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_models": "deepseek-v4-flash,deepseek-v4-pro",
        "deepseek_base_url": "https://api.deepseek.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_deepseek_is_a_real_openai_compatible_provider(tmp_path):
    provider = get_provider("deepseek", _settings(tmp_path))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == "deepseek"
    assert provider.model == "deepseek-v4-flash"
    assert provider.base_url == "https://api.deepseek.com"


def test_deepseek_preflight_uses_json_generation(tmp_path):
    with patch("app.services.provider_preflight.httpx.post", return_value=FakeResponse()):
        result = preflight_provider("deepseek", _settings(tmp_path))
    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.model == "deepseek-v4-flash"
    assert "deepseek-v4-pro" in result.valid_routes


def test_custom_provider_metadata_does_not_persist_api_key(tmp_path):
    settings = _settings(tmp_path, deepseek_api_key=None)
    spec = save_custom_provider(
        provider_id="provedor-interno",
        label="Provedor Interno",
        base_url="https://ia.interna.example/v1",
        default_model="modelo-a",
        models=["modelo-a", "modelo-b"],
        api_key="secret-value-that-must-not-be-in-json",
        enabled=True,
        tier="custom",
        priority=40,
        settings=settings,
    )
    registry_text = Path(settings.ai_provider_registry_path).read_text(encoding="utf-8")
    env_text = Path(settings.ai_settings_env_path).read_text(encoding="utf-8")
    assert spec.id == "provedor-interno"
    assert "secret-value-that-must-not-be-in-json" not in registry_text
    assert "AI_PROVIDER_PROVEDOR_INTERNO_API_KEY=secret-value-that-must-not-be-in-json" in env_text
    assert Path(settings.ai_provider_registry_path).stat().st_mode & 0o777 == 0o600
    assert Path(settings.ai_settings_env_path).stat().st_mode & 0o777 == 0o600


def test_public_registry_masks_all_credentials(tmp_path):
    settings = _settings(tmp_path)
    payload = json.dumps(public_registry(settings))
    assert "deepseek-secret" not in payload
    assert '"configured": true' in payload


def test_env_update_preserves_existing_values_and_permissions(tmp_path):
    settings = _settings(tmp_path)
    path = Path(settings.ai_settings_env_path)
    path.write_text("POSTGRES_DSN=sqlite+pysqlite:///:memory:\nKEEP_ME=yes\n", encoding="utf-8")
    update_env_values({"DEEPSEEK_API_KEY": "new-secret"}, settings=settings)
    text = path.read_text(encoding="utf-8")
    assert "KEEP_ME=yes" in text
    assert "DEEPSEEK_API_KEY=new-secret" in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_dynamic_provider_ids_are_accepted_by_web_payload():
    with patch(
        "app.web_settings.provider_spec",
        return_value=SimpleNamespace(enabled=True),
    ):
        enable_dynamic_provider_payload()
        payload = InvestigationPayload(
            target="192.0.2.10",
            objective="validar serviço",
            provider="provedor-interno",
        )
    assert payload.provider == "provedor-interno"
