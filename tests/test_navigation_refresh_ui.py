from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_navigation_refresh_moves_utilities_out_of_primary_tabs() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "navigation-refresh.js").read_text(encoding="utf-8")
    required = (
        "view === 'replay'",
        "view === 'playbooks'",
        "view === 'health'",
        "inventory-open-playbooks",
        "settings-open-health",
        "restoreSimpleAnalysisForm",
        "iconPaths",
        "noc:",
        "investigations:",
    )
    for item in required:
        assert item in source


def test_navigation_refresh_promotes_user_actions_to_single_global_header() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "navigation-refresh.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "app" / "ui" / "navigation-refresh.css").read_text(encoding="utf-8")
    assert "promoteGlobalHeaderActions" in source
    assert "document.querySelector('.sidebar-safety')?.remove()" in source
    assert "actions.classList.add('global-header-actions')" in source
    assert "Nova investigação" in source
    assert "keepNewInvestigationVisible" in source
    assert ".top-navigation-layout .topbar" in css
    assert "display: none !important" in css
    assert ".global-header-actions" in css


def test_checkmk_central_panel_keeps_long_status_visible() -> None:
    css = (PROJECT_ROOT / "app" / "ui" / "fleet-ui.css").read_text(encoding="utf-8")
    compact = "".join(css.split())
    assert ".fleet-panel" in css
    assert "overflow:visible" in compact
    assert "overflow-wrap:anywhere" in compact
    assert ".cmk-error-copy" in css


def test_execution_visibility_exposes_access_journey() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "execution-visibility.js").read_text(encoding="utf-8")
    assert "SSH / VPN passo a passo" in source
    assert "access_journey" in source
    assert "/ui/api/executions/" in source
    assert "Ver eventos da investigação" in source


def test_versioned_ui_injects_new_assets() -> None:
    source = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    for asset in (
        "navigation-refresh.css",
        "navigation-refresh.js",
        "fleet-scope.css",
        "fleet-scope.js",
        "execution-visibility.css",
        "execution-visibility.js",
    ):
        assert asset in source

    web_main = (PROJECT_ROOT / "app" / "web_main.py").read_text(encoding="utf-8")
    assert "fleet-ui.css?v={_ASSET_VERSION}" in web_main
    assert "fleet-ui.js?v={_ASSET_VERSION}" in web_main
