from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_site_detail_supports_host_problem_filter_and_show_all() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "fleet-ui.js").read_text(encoding="utf-8")
    assert "data-cmk-host-filter" in source
    assert "renderSiteProblems" in source
    assert "Problemas de ${selected}" in source
    assert "cmk-show-all-problems" in source


def test_noc_ui_has_history_filters_and_configurable_categories() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "fleet-ui.js").read_text(encoding="utf-8")
    assert 'data-cmk-tab="history"' in source
    assert "Precisa fazer manualmente" in source
    assert "Sem acesso" in source
    assert "Correções automáticas" in source
    assert "/ui/api/noc/policies" in source
    assert "/ui/api/noc/history" in source


def test_checkmk_patrol_is_automatic_at_two_minutes_by_default() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "checkmk_master_patrol.py").read_text(encoding="utf-8")
    assert '"CHECKMK_MASTER_POLL_INTERVAL_SECONDS", 120' in source
    assert "time.sleep(int(cfg[\"poll_interval\"]))" in source
    assert "start_checkmk_master_patrol_background" in source


def test_browser_does_not_poll_fleet_while_noc_screen_is_hidden() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "fleet-ui.js").read_text(encoding="utf-8")
    assert "function isNocActive()" in source
    assert "if (!showError && !isNocActive()) return" in source
    assert "if (document.hidden || !isNocActive()) return" in source
    assert "}, 30000);" in source


def test_clients_are_materialized_from_checkmk_inventory() -> None:
    sync_source = (PROJECT_ROOT / "app" / "services" / "checkmk_customer_sync.py").read_text(encoding="utf-8")
    web_source = (PROJECT_ROOT / "app" / "web_operator_experience.py").read_text(encoding="utf-8")
    assert "CheckmkSiteORM" in sync_source
    assert "CheckmkHostORM" in sync_source
    assert "_upsert_customer" in sync_source
    assert "_upsert_node" in sync_source
    assert "sync_checkmk_customers_from_inventory()" in web_source


def test_incident_css_prevents_detail_clipping() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "noc-automation.css").read_text(encoding="utf-8")
    assert ".noc-grid>*{min-width:0}" in source
    assert ".noc-detail{min-width:0;max-width:100%;overflow:hidden}" in source
    assert "overflow-wrap:anywhere" in source
