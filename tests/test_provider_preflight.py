from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from app.cli.agent import doctor_app
from app.core.policies import EnvironmentType
from app.core.settings import Settings
from app.services.ai_providers import ProviderError
from app.services.provider_preflight import (
    ProviderPreflight,
    ProviderState,
    preflight_all,
    preflight_provider,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", "http://service.invalid")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(
                self.status_code,
                request=self.request,
                json=self._payload,
            )
            raise httpx.HTTPStatusError(
                "erro",
                request=self.request,
                response=response,
            )


def _settings(**overrides) -> Settings:
    values = {
        "postgres_dsn": "sqlite+pysqlite:///:memory:",
        "ai_preflight_timeout_seconds": 2,
        "gemini_api_key": "gemini-secret",
        "gemini_model": "gemini-2.5-flash",
        "gemini_auto_free": True,
        "gemini_transient_fallback": True,
        "gemini_free_models": (
            "gemini-3.5-flash,gemini-3.1-flash-lite,"
            "gemini-2.5-flash,gemini-2.5-flash-lite"
        ),
        "ollama_base_url": "http://ollama.invalid",
        "ollama_model": "gemma3:4b",
        "ollama_auto_fallback": True,
        "ollama_preferred_models": "gemma3:4b,llama3.2",
        "ollama_preflight_timeout_seconds": 60,
        "omniroute_api_key": "endpoint-secret",
        "omniroute_base_url": "http://omniroute.invalid/v1",
        "omniroute_default_route": "auto",
        "omniroute_routes": "Balanceado=auto,Código=auto/coding",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _gemini_models(*names: str) -> FakeResponse:
    return FakeResponse(
        {
            "models": [
                {
                    "name": f"models/{name}",
                    "supportedGenerationMethods": ["generateContent"],
                }
                for name in names
            ]
        }
    )


def _gemini_success() -> FakeResponse:
    return FakeResponse(
        {
            "candidates": [
                {"content": {"parts": [{"text": '{"preflight": true}'}]}}
            ]
        }
    )


def test_ollama_available_only_when_exact_model_and_json_probe_work():
    tags = FakeResponse({"models": [{"name": "gemma3:4b"}]})
    generation = FakeResponse({"response": '{"preflight": true}'})

    with patch("app.services.provider_preflight.httpx.get", return_value=tags), patch(
        "app.services.provider_preflight.httpx.post",
        return_value=generation,
    ) as post:
        result = preflight_provider("ollama", _settings())

    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.model == "gemma3:4b"
    assert result.valid_routes == ("gemma3:4b",)
    assert post.call_args.kwargs["timeout"] == 60


def test_ollama_quick_probe_does_not_wait_for_generation():
    tags = FakeResponse({"models": [{"name": "llama3.2"}]})

    with patch("app.services.provider_preflight.httpx.get", return_value=tags), patch(
        "app.services.provider_preflight.httpx.post"
    ) as generation:
        result = preflight_provider(
            "ollama",
            _settings(ollama_model="llama3.2"),
            quick=True,
        )

    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.model == "llama3.2"
    assert "validada antes de abrir o SSH" in result.detail
    generation.assert_not_called()


def test_preflight_all_uses_quick_catalog_by_default():
    diagnostic = ProviderPreflight(
        provider="gemini",
        label="Gemini",
        state=ProviderState.AVAILABLE,
        model="model",
        detail="ok",
        selectable=True,
    )

    with patch(
        "app.services.provider_preflight.preflight_provider",
        return_value=diagnostic,
    ) as provider:
        result = preflight_all(_settings())

    assert len(result) == 5
    assert all(call.kwargs["quick"] is True for call in provider.call_args_list)


def test_ollama_falls_back_to_installed_preferred_model():
    tags = FakeResponse({"models": [{"name": "gemma3:4b"}]})
    generation = FakeResponse({"response": '{"preflight": true}'})

    with patch("app.services.provider_preflight.httpx.get", return_value=tags), patch(
        "app.services.provider_preflight.httpx.post",
        return_value=generation,
    ):
        result = preflight_provider(
            "ollama",
            _settings(ollama_model="llama3.2"),
        )

    assert result.state == ProviderState.DEGRADED
    assert result.selectable is True
    assert result.model == "gemma3:4b"
    assert "llama3.2" in result.detail
    assert result.invalid_routes == ("llama3.2",)


def test_ollama_rejects_explicit_model_that_is_not_installed():
    tags = FakeResponse({"models": [{"name": "gemma3:4b"}]})

    with patch("app.services.provider_preflight.httpx.get", return_value=tags), patch(
        "app.services.provider_preflight.httpx.post"
    ) as generation:
        result = preflight_provider("ollama", _settings(), "llama3.2")

    assert result.state == ProviderState.MISCONFIGURED
    assert result.selectable is False
    assert "não está instalado" in result.detail
    assert result.valid_routes == ("gemma3:4b",)
    generation.assert_not_called()


def test_ollama_reports_unavailable_service_without_raw_traceback():
    request = httpx.Request("GET", "http://ollama.invalid/api/tags")
    with patch(
        "app.services.provider_preflight.httpx.get",
        side_effect=httpx.ConnectError("connection refused secret-value", request=request),
    ):
        result = preflight_provider("ollama", _settings())

    assert result.state == ProviderState.UNAVAILABLE
    assert "secret-value" not in result.detail


def test_ollama_timeout_explains_cold_start_and_specific_setting():
    tags = FakeResponse({"models": [{"name": "llama3.2"}]})
    request = httpx.Request("POST", "http://ollama.invalid/api/generate")

    with patch("app.services.provider_preflight.httpx.get", return_value=tags), patch(
        "app.services.provider_preflight.httpx.post",
        side_effect=httpx.ReadTimeout("slow", request=request),
    ):
        result = preflight_provider(
            "ollama",
            _settings(ollama_model="llama3.2", ollama_preflight_timeout_seconds=45),
        )

    assert result.state == ProviderState.UNAVAILABLE
    assert "45s" in result.detail
    assert "OLLAMA_PREFLIGHT_TIMEOUT_SECONDS" in result.detail


def test_gemini_auto_selects_newest_known_free_model_visible_to_key():
    models = _gemini_models("gemini-2.5-flash", "gemini-3.5-flash")
    generation = _gemini_success()

    with patch("app.services.provider_preflight.httpx.get", return_value=models), patch(
        "app.services.provider_preflight.httpx.post",
        return_value=generation,
    ) as post:
        result = preflight_provider("gemini", _settings())

    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.model == "gemini-3.5-flash"
    assert result.valid_routes == ("gemini-3.5-flash", "gemini-2.5-flash")
    assert "automaticamente" in result.detail
    assert "gemini-3.5-flash" in post.call_args.args[0]


def test_gemini_quick_probe_lists_provider_without_generation():
    models = _gemini_models("gemini-2.5-flash")

    with patch("app.services.provider_preflight.httpx.get", return_value=models), patch(
        "app.services.provider_preflight.httpx.post"
    ) as generation:
        result = preflight_provider("gemini", _settings(), quick=True)

    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.model == "gemini-2.5-flash"
    assert "geração será validada antes de abrir o SSH" in result.detail
    generation.assert_not_called()


def test_gemini_falls_back_when_preferred_model_has_high_demand():
    models = _gemini_models("gemini-2.5-flash", "gemini-2.5-flash-lite")
    overloaded = FakeResponse(
        {"error": {"message": "This model is currently experiencing high demand."}},
        status_code=503,
    )

    with patch("app.services.provider_preflight.httpx.get", return_value=models), patch(
        "app.services.provider_preflight.httpx.post",
        side_effect=[overloaded, _gemini_success()],
    ) as post:
        result = preflight_provider(
            "gemini",
            _settings(
                gemini_model="gemini-2.5-flash",
                gemini_free_models="gemini-2.5-flash,gemini-2.5-flash-lite",
            ),
        )

    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.model == "gemini-2.5-flash-lite"
    assert "HTTP 503" in result.detail
    assert "fallback automático" in result.detail
    assert post.call_count == 2


def test_gemini_keeps_explicit_free_model_when_it_works():
    models = _gemini_models("gemini-2.5-flash", "gemini-3.5-flash")

    with patch("app.services.provider_preflight.httpx.get", return_value=models), patch(
        "app.services.provider_preflight.httpx.post",
        return_value=_gemini_success(),
    ):
        result = preflight_provider("gemini", _settings(), "gemini-2.5-flash")

    assert result.state == ProviderState.AVAILABLE
    assert result.model == "gemini-2.5-flash"


def test_gemini_reports_all_transient_failures_without_credentials():
    models = _gemini_models("gemini-2.5-flash", "gemini-2.5-flash-lite")
    overloaded = FakeResponse(
        {"error": {"message": "temporary high demand"}},
        status_code=503,
    )
    rate_limited = FakeResponse(
        {"error": {"message": "quota spike"}},
        status_code=429,
    )

    with patch("app.services.provider_preflight.httpx.get", return_value=models), patch(
        "app.services.provider_preflight.httpx.post",
        side_effect=[overloaded, rate_limited],
    ):
        result = preflight_provider(
            "gemini",
            _settings(
                gemini_model="gemini-2.5-flash",
                gemini_free_models="gemini-2.5-flash,gemini-2.5-flash-lite",
            ),
        )

    assert result.state == ProviderState.UNAVAILABLE
    assert "gemini-2.5-flash (HTTP 503)" in result.detail
    assert "gemini-2.5-flash-lite (HTTP 429)" in result.detail
    assert "gemini-secret" not in result.detail


def test_gemini_reports_public_api_error_message_without_credentials():
    models = _gemini_models("gemini-3.5-flash")
    rejected = FakeResponse(
        {"error": {"message": "model is unavailable for this API version"}},
        status_code=404,
    )

    with patch("app.services.provider_preflight.httpx.get", return_value=models), patch(
        "app.services.provider_preflight.httpx.post",
        return_value=rejected,
    ):
        result = preflight_provider("gemini", _settings())

    assert result.state == ProviderState.MISCONFIGURED
    assert "model is unavailable" in result.detail
    assert "gemini-secret" not in result.detail


def test_omniroute_requires_endpoint_token_before_network():
    with patch("app.services.provider_preflight.httpx.get") as request:
        result = preflight_provider(
            "omniroute",
            _settings(omniroute_api_key=None),
        )

    assert result.state == ProviderState.NOT_CONFIGURED
    assert result.selectable is False
    assert "OMNIROUTE_API_KEY" in result.detail
    request.assert_not_called()


def test_omniroute_requires_at_least_one_configured_route():
    with patch("app.services.provider_preflight.httpx.get") as request:
        result = preflight_provider(
            "omniroute",
            _settings(
                omniroute_default_route="",
                omniroute_routes="",
                omniroute_model="",
            ),
        )

    assert result.state == ProviderState.MISCONFIGURED
    assert result.selectable is False
    request.assert_not_called()


def test_omniroute_rejects_route_missing_from_models_endpoint():
    response = FakeResponse({"data": [{"id": "auto/coding"}]})
    with patch("app.services.provider_preflight.httpx.get", return_value=response):
        result = preflight_provider("omniroute", _settings(), "auto")

    assert result.state == ProviderState.MISCONFIGURED
    assert result.selectable is False
    assert "não existe" in result.detail


def test_omniroute_accepts_route_published_by_models_endpoint():
    response = FakeResponse({"data": [{"id": "auto/coding"}, {"id": "auto/fast"}]})
    settings = _settings(
        omniroute_default_route="auto/coding",
        omniroute_routes="Código=auto/coding,Rápido=auto/fast",
    )
    with patch("app.services.provider_preflight.httpx.get", return_value=response):
        result = preflight_provider("omniroute", settings)

    assert result.state == ProviderState.AVAILABLE
    assert result.selectable is True
    assert result.valid_routes == ("auto/coding", "auto/fast")


def test_omniroute_reports_unavailable_gateway():
    request = httpx.Request("GET", "http://omniroute.invalid/v1/models")
    with patch(
        "app.services.provider_preflight.httpx.get",
        side_effect=httpx.ConnectError("refused", request=request),
    ):
        result = preflight_provider("omniroute", _settings())

    assert result.state == ProviderState.UNAVAILABLE
    assert result.selectable is False


def test_doctor_ai_masks_credentials_and_renders_state():
    diagnostic = ProviderPreflight(
        provider="omniroute",
        label="OmniRoute",
        state=ProviderState.AVAILABLE,
        model="auto",
        detail="Token, endpoint e rota validados.",
        latency_ms=12,
        selectable=True,
    )
    runner = CliRunner()

    with patch("app.cli.agent.preflight_all", return_value=[diagnostic]):
        result = runner.invoke(doctor_app, [])

    assert result.exit_code == 0
    assert "disponível" in result.stdout
    assert "auto" in result.stdout
    assert "endpoint-secret" not in result.stdout
    assert "Nenhuma senha, token ou API key é exibida" in result.stdout


def test_run_target_stops_before_resolution_and_ssh_when_provider_fails():
    settings = _settings()
    with patch(
        "app.services.runner.require_selected_provider",
        side_effect=ProviderError("provedor indisponível"),
    ), patch("app.services.runner.resolve_target") as resolver, patch(
        "app.services.runner.build_executor"
    ) as executor:
        from app.services.runner import run_target

        with pytest.raises(ProviderError, match="provedor indisponível"):
            run_target(
                "192.0.2.10",
                "validar",
                environment=EnvironmentType.TRAINING,
                settings=settings,
            )

    resolver.assert_not_called()
    executor.assert_not_called()
