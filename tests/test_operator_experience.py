from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from app.services.replay_scenarios import get_replay_scenario, list_replay_scenarios
from app.web_replay import _prepare_replay_result
from app.web_ui_cache import _inject_operator_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_operator_assets_are_injected_and_versioned() -> None:
    html = "<html><head></head><body></body></html>"
    rendered = _inject_operator_assets(html)
    assert "/ui/assets/operator-experience.css?v=" in rendered
    assert "/ui/assets/ui-core.js?v=" in rendered
    assert "/ui/assets/operator-experience.js?v=" in rendered
    assert rendered.index("operator-experience.css") < rendered.index("</head>")
    assert rendered.index("ui-core.js") < rendered.index("operator-experience.js")
    assert rendered.index("operator-experience.js") < rendered.index("</body>")


def test_replay_catalog_is_sanitized_and_does_not_contain_secrets() -> None:
    scenarios = list_replay_scenarios()
    assert len(scenarios) >= 4
    ids = {item["id"] for item in scenarios}
    assert {"checkmk-automation-helper", "vpn-flapping", "multi-host-standby-monitor", "snmp-timeout"} <= ids
    source = (PROJECT_ROOT / "app" / "services" / "replay_scenarios.py").read_text(encoding="utf-8").casefold()
    for forbidden in ("ssh_default_password", "ssh_srv_vpn_senha", "api_key=", "password=", "community="):
        assert forbidden not in source


def test_replay_result_marks_local_mode_and_keeps_multi_host_context() -> None:
    scenario = get_replay_scenario("multi-host-standby-monitor")
    assert scenario is not None
    result = _prepare_replay_result(scenario)
    assert result["replay"]["enabled"] is True
    assert result["replay"]["sanitized"] is True
    assert result["replay"]["connected_to_real_target"] is False
    assert result["selected_provider"] == "replay"
    assert result["multi_host"]["enabled"] is True
    assert result["multi_host"]["root_host"] == "172.27.250.31"
    assert "Próximo passo mais seguro" in result["ticket_report"]


def test_operator_interface_contains_requested_ux_flows() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "operator-experience.js").read_text(encoding="utf-8")
    stylesheet = (PROJECT_ROOT / "app" / "ui" / "operator-experience.css").read_text(encoding="utf-8")
    required = (
        "Cliente e alerta",
        "Escopo e rota",
        "Estratégia",
        "Revisão",
        "/ui/api/customers",
        "/ui/api/replay/scenarios",
        "new EventSource",
        "Host do alerta",
        "Host da causa provável",
        "Host de eventual correção",
        "Evidências",
        "Caminho da investigação",
        "Comunicação",
        "Atualização técnica",
        "Transferência para Infra",
        "Mensagem simplificada",
        "Tentar novamente em nova análise",
        "Editar IP/porta",
        "Salvar escopo",
        "Repetir investigação",
        "agent-ui-favorite-scopes",
        "agent-ui-recent-scopes",
    )
    for item in required:
        assert item in script
    for css_class in (
        ".investigation-wizard",
        ".customer-grid",
        ".replay-grid",
        ".operator-result-tabs",
        ".host-triad",
        ".communication-grid",
        ".observability-grid",
    ):
        assert css_class in stylesheet


def test_operator_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não está instalado no ambiente de testes")
    for name in ("ui-core.js", "operator-experience.js"):
        path = PROJECT_ROOT / "app" / "ui" / name
        result = subprocess.run(
            [node, "--check", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_replay_and_customer_routes_are_registered() -> None:
    main = (PROJECT_ROOT / "app" / "web_main.py").read_text(encoding="utf-8")
    replay = (PROJECT_ROOT / "app" / "web_replay.py").read_text(encoding="utf-8")
    customers = (PROJECT_ROOT / "app" / "web_operator_experience.py").read_text(encoding="utf-8")
    assert "replay_router" in main
    assert "operator_experience_router" in main
    assert '"/ui/api/replay/scenarios"' in replay
    assert '"/ui/api/replay/{scenario_id}"' in replay
    assert '"/ui/api/customers"' in customers
    assert '"/ui/api/customers/{customer_id}"' in customers
    assert "submit_ui_execution" in replay
    assert "Nenhuma conexão real" in replay
