from __future__ import annotations

from pathlib import Path

from app.services.fleet_patrol import _parse_patrol_output, _state_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_patrol_parser_reads_non_ok_services_and_hosts() -> None:
    output = """
PATROL_CONTEXT|local|cal
PATROL_SERVICE|cal-db01;192.168.1.10;Check_MK Agent;2;1780000000;Connection refused
PATROL_HOST|cal-fw01;192.168.1.1;1;1780000001;Host DOWN
"""
    items = _parse_patrol_output(output)
    assert len(items) == 2
    assert items[0]["site"] == "cal"
    assert items[0]["host"] == "cal-db01"
    assert items[0]["service"] == "Check_MK Agent"
    assert items[0]["state"] == 2
    assert items[1]["service"] == "Host status"
    assert items[1]["state"] == 1


def test_patrol_state_names_match_checkmk_states() -> None:
    assert _state_name(1) == "WARN"
    assert _state_name(2) == "CRIT"
    assert _state_name(3) == "UNKNOWN"


def test_worker_uses_checkmk_master_and_does_not_start_legacy_patrol() -> None:
    source = (PROJECT_ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    assert "start_checkmk_master_patrol_background" in source
    assert "resume_active_fleet_discovery" in source
    assert "start_fleet_patrol_background" not in source
    assert "start_fleet_discovery_background" not in source


def test_noc_ui_prioritizes_checkmk_and_keeps_network_discovery_as_contingency() -> None:
    web_source = (PROJECT_ROOT / "app" / "web_fleet.py").read_text(encoding="utf-8")
    ui_source = (PROJECT_ROOT / "app" / "ui" / "fleet-ui.js").read_text(encoding="utf-8")
    assert "/ui/api/noc/checkmk-master/sync" in web_source
    assert "/ui/api/noc/checkmk-master/poll" in web_source
    assert "/ui/api/noc/fleet/start" in web_source
    assert "Checkmk Central" in ui_source
    assert "Sincronizar Checkmk" in ui_source
    assert "Descoberta de rede" in ui_source
    assert "contingência" in ui_source
