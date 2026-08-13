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


def test_navigation_refresh_observer_does_not_react_to_its_own_svg_mutations() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "navigation-refresh.js").read_text(encoding="utf-8")
    assert "holder.dataset.navIconView !== view" in source
    assert "function nodeAddsNavigationItem" in source
    assert "mutation.addedNodes" in source
    assert "node.matches('.nav-item')" in source
    assert "if (!navigationChanged) return" in source
    assert "new MutationObserver(() =>" not in source


def test_checkmk_central_panel_keeps_long_status_visible() -> None:
    css = PROJECT_ROOT / "app" / "ui" / "fleet-ui.css"
    assert css.is_file()
    text = css.read_text(encoding="utf-8")
    compact = "".join(text.split())
    assert ".fleet-panel" in text
    assert "overflow:visible" in compact
    assert "overflow-wrap:anywhere" in compact
    assert ".cmk-error-copy" in text


def test_execution_visibility_exposes_access_journey() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "execution-visibility.js").read_text(encoding="utf-8")
    assert "SSH / VPN passo a passo" in source
    assert "access_journey" in source
    assert "/ui/api/executions/" in source
    assert "Ver eventos da investigação" in source


def test_versioned_ui_injects_all_runtime_assets_without_response_middleware() -> None:
    source = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    for asset in (
        "navigation-refresh.css",
        "navigation-refresh.js",
        "fleet-scope.css",
        "fleet-scope.js",
        "execution-visibility.css",
        "execution-visibility.js",
        "fleet-ui.css",
        "fleet-ui.js",
        "noc-automation.css",
        "n2-workspace.css",
        "n2-persistence.css",
        "n2-documentation.js",
        "navigation-policy.js",
    ):
        assert asset in source

    assert "n2-workspace.js" not in source
    assert "def _inject_n2_shell" in source

    web_main = (PROJECT_ROOT / "app" / "web_main.py").read_text(encoding="utf-8")
    assert '@app.middleware("http")' not in web_main
    assert "app.include_router(ui_cache_router)" in web_main


def test_asset_cache_key_tracks_checkout_not_stale_installed_metadata() -> None:
    source = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    assert "def _project_version()" in source
    assert "pyproject.toml" in source
    assert "def _git_revision()" in source
    assert 'git", "rev-parse", "--short=12", "HEAD"' in source
    assert 'return f"{version}-{revision}" if revision else version' in source
