from __future__ import annotations

from pathlib import Path

from app.services.checkmk_operational import (
    _host_state_name,
    _livestatus_queries,
    _problem_key,
    _service_state_name,
    _snapshot_script,
)
from app.core.settings import get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_problem_key_is_isolated_by_site() -> None:
    hot = _problem_key("hot", "service", "db01", "Memory")
    dca = _problem_key("dca", "service", "db01", "Memory")
    assert hot != dca
    assert hot.startswith("hot|")
    assert dca.startswith("dca|")


def test_checkmk_state_names_preserve_service_and_host_semantics() -> None:
    assert _service_state_name(1) == "WARN"
    assert _service_state_name(2) == "CRIT"
    assert _service_state_name(3) == "UNKNOWN"
    assert _host_state_name(1) == "DOWN"
    assert _host_state_name(2) == "UNREACHABLE"


def test_livestatus_queries_use_real_line_breaks_and_terminate_with_blank_line() -> None:
    hosts, services = _livestatus_queries()
    assert "GET hosts\nColumns: name address state\n" in hosts
    assert "GET services\nColumns: host_name host_address description state plugin_output\n" in services
    assert "\\n" not in hosts
    assert "\\n" not in services
    assert hosts.endswith("\n\n")
    assert services.endswith("\n\n")
    assert "Filter: state = 1\n" in services
    assert "Filter: state = 2\n" in services
    assert "Filter: state = 3\n" in services
    assert "Or: 3\n" in services


def test_snapshot_queries_hosts_and_non_ok_services_in_same_cycle() -> None:
    source = _snapshot_script(settings=get_settings())
    assert "GET hosts" in source
    assert "Columns: name address state" in source
    assert "GET services" in source
    assert "host_name host_address description state plugin_output" in source
    assert "Filter: state = 1" in source
    assert "Filter: state = 2" in source
    assert "Filter: state = 3" in source
    assert "Or: 3" in source
    assert "GET hosts retornou zero linhas" in source
    assert "Livestatus encerrou a conexao sem payload" in source
    assert '"site_snapshot"' in source
    assert "ThreadPoolExecutor" in source


def test_patrol_feeds_snapshot_into_incidents_and_jobs() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "checkmk_master_patrol.py").read_text(encoding="utf-8")
    assert "collect_checkmk_operational_snapshot" in source
    assert "register_checkmk_event" in source
    assert "enqueue_investigation" in source
    assert "update_problem_automation" in source
    assert '"site_scope": True' in source


def test_ui_exposes_problems_sites_failures_and_site_detail() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "fleet-ui.js").read_text(encoding="utf-8")
    web = (PROJECT_ROOT / "app" / "web_fleet.py").read_text(encoding="utf-8")
    for text in ("Problemas", "Sites", "Sem resposta", "Host / IP", "Skill", "Automação"):
        assert text in source
    assert "/ui/api/noc/checkmk-master/overview" in source
    assert "/ui/api/noc/checkmk-master/sites/" in source
    assert "/ui/api/noc/checkmk-master/overview" in web
    assert "/ui/api/noc/checkmk-master/sites/{site_id}" in web
