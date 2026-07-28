from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.settings import Settings
from app.services.opencode_cli import (
    OpenCodeError,
    _build_run_command,
    _parse_run_output,
    ensure_opencode_config,
    launch_opencode_web,
    opencode_config,
    opencode_status,
    selected_opencode_model,
    submit_opencode_run,
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
        "opencode_interface_enabled": True,
        "opencode_interface_allow_build": True,
        "opencode_run_concurrency": 1,
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
    assert status.models == (
        {"value": "auto/coding", "label": "Código"},
        {"value": "auto/fast", "label": "Rápido"},
    )
    assert status.interface_enabled is True
    assert status.allow_build is True


def test_web_requires_password_even_on_localhost(tmp_path):
    settings = _settings(tmp_path, opencode_server_password=None)
    fake_status = SimpleNamespace(command="/bin/true", workdir=str(tmp_path))
    with patch("app.services.opencode_cli.opencode_status", return_value=fake_status):
        with pytest.raises(OpenCodeError, match="OPENCODE_SERVER_PASSWORD"):
            launch_opencode_web(settings)


def test_embedded_command_attaches_to_server_and_uses_selected_route(tmp_path):
    status = SimpleNamespace(
        command="/usr/local/bin/opencode",
        web_reachable=True,
        web_url="http://127.0.0.1:4096",
        workdir=str(tmp_path),
    )
    command = _build_run_command(
        status=status,
        prompt="Revise os testes",
        agent="plan",
        model="auto/coding",
        session_id="ses_123",
        auto_approve=False,
    )

    assert command[:3] == ["/usr/local/bin/opencode", "run", "--format"]
    assert ["--agent", "plan"] == command[command.index("--agent"):command.index("--agent") + 2]
    assert ["--model", "omniroute/auto/coding"] == command[command.index("--model"):command.index("--model") + 2]
    assert ["--attach", "http://127.0.0.1:4096"] == command[command.index("--attach"):command.index("--attach") + 2]
    assert ["--session", "ses_123"] == command[command.index("--session"):command.index("--session") + 2]
    assert "--auto" not in command
    assert command[-1] == "Revise os testes"


def test_build_command_only_enables_auto_after_explicit_confirmation(tmp_path):
    status = SimpleNamespace(
        command="/usr/local/bin/opencode",
        web_reachable=False,
        web_url="http://127.0.0.1:4096",
        workdir=str(tmp_path),
    )
    command = _build_run_command(
        status=status,
        prompt="Aplique a correção",
        agent="build",
        model="auto/coding",
        session_id=None,
        auto_approve=True,
    )
    assert "--auto" in command
    assert "--attach" not in command


def test_json_events_are_parsed_and_secret_fields_removed():
    raw = "\n".join(
        (
            '{"type":"message","sessionID":"ses_abc","content":"Análise concluída"}',
            '{"type":"tool","authorization":"Bearer hidden","output":"pytest passou"}',
        )
    )
    events, output, session_id = _parse_run_output(raw, 10000)

    assert session_id == "ses_abc"
    assert "Análise concluída" in output
    assert "pytest passou" in output
    assert "Bearer hidden" not in json.dumps(events)
    assert "authorization" not in json.dumps(events)


def test_submit_rejects_route_not_configured_in_omniroute(tmp_path):
    settings = _settings(tmp_path)
    with patch("app.services.opencode_cli.omniroute_route_options", return_value=_routes()):
        with pytest.raises(OpenCodeError, match="rota selecionada"):
            submit_opencode_run(
                "Analise o projeto",
                model="modelo-inexistente",
                settings=settings,
            )


def test_submit_rejects_build_when_interface_disables_changes(tmp_path):
    settings = _settings(tmp_path, opencode_interface_allow_build=False)
    with patch("app.services.opencode_cli.omniroute_route_options", return_value=_routes()):
        with pytest.raises(OpenCodeError, match="modo aplicar"):
            submit_opencode_run(
                "Aplique a correção",
                agent="build",
                settings=settings,
            )
