from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.settings import Settings
from app.services.opencode_cli import (
    OpenCodeError,
    ensure_opencode_config,
    launch_opencode_web,
    opencode_config,
    opencode_status,
    selected_opencode_model,
)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "postgres_dsn": "sqlite+pysqlite:///:memory:",
        "omniroute_api_key": "endpoint-secret-value",
        "omniroute_base_url": "http://127.0.0.1:20128/v1",
        "omniroute_default_route": "auto/coding",
        "omniroute_routes": "Código=auto/coding,Rápido=auto/fast",
        "opencode_enabled": True,
        "opencode_cli_path": "/usr/local/bin/opencode",
        "opencode_workdir": str(tmp_path),
        "opencode_config_path": str(tmp_path / "config" / "opencode.json"),
        "opencode_model": "auto/coding",
        "opencode_small_model": "auto/fast",
        "opencode_server_password": "web-password",
        "opencode_tunnel_host": "192.168.28.10",
        "opencode_tunnel_ssh_port": 2222,
        "opencode_tunnel_user": "jose",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _routes():
    return [
        SimpleNamespace(model="auto/coding", label="Código", is_default=True),
        SimpleNamespace(model="auto/fast", label="Rápido", is_default=False),
    ]


def test_config_uses_omniroute_without_persisting_token(tmp_path):
    settings = _settings(tmp_path)
    with patch("app.services.opencode_cli.omniroute_route_options", return_value=_routes()):
        payload = opencode_config(settings)

    serialized = json.dumps(payload)
    provider = payload["provider"]["omniroute"]
    assert payload["enabled_providers"] == ["omniroute"]
    assert payload["model"] == "omniroute/auto/coding"
    assert payload["small_model"] == "omniroute/auto/fast"
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "{env:OPENCODE_OMNIROUTE_BASE_URL}"
    assert provider["options"]["apiKey"] == "{env:OMNIROUTE_API_KEY}"
    assert "endpoint-secret-value" not in serialized
    assert payload["permission"]["edit"] == "ask"
    assert payload["permission"]["bash"] == "ask"
    assert payload["permission"]["external_directory"] == "deny"


def test_config_file_is_generated_with_restricted_permissions(tmp_path):
    settings = _settings(tmp_path)
    with patch("app.services.opencode_cli.omniroute_route_options", return_value=_routes()):
        path = ensure_opencode_config(settings)

    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["share"] == "disabled"
    assert "endpoint-secret-value" not in path.read_text(encoding="utf-8")


def test_selected_model_uses_configured_route(tmp_path):
    settings = _settings(tmp_path, opencode_model="auto/fast")
    with patch("app.services.opencode_cli.omniroute_route_options", return_value=_routes()):
        assert selected_opencode_model(settings) == "auto/fast"


def test_status_is_secret_free_and_builds_exact_tunnel(tmp_path):
    settings = _settings(tmp_path)
    with patch("app.services.opencode_cli.resolve_opencode_command", return_value=None), patch(
        "app.services.opencode_cli.omniroute_route_options",
        return_value=_routes(),
    ), patch("app.services.opencode_cli._web_reachable", return_value=False):
        status = opencode_status(settings)

    serialized = json.dumps(asdict(status))
    assert "endpoint-secret-value" not in serialized
    assert "web-password" not in serialized
    assert status.tunnel_command == "ssh -N -L 4096:127.0.0.1:4096 jose@192.168.28.10 -p 2222"


def test_web_requires_password_even_on_localhost(tmp_path):
    settings = _settings(tmp_path, opencode_server_password=None)
    fake_status = SimpleNamespace(command="/bin/true", workdir=str(tmp_path))
    with patch("app.services.opencode_cli.opencode_status", return_value=fake_status):
        with pytest.raises(OpenCodeError, match="OPENCODE_SERVER_PASSWORD"):
            launch_opencode_web(settings)
