from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"


def test_v146_assets_are_published_last() -> None:
    cache = (ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    assert (UI / "noc-memory-ui-v146.js").is_file()
    assert (UI / "noc-memory-ui-v146.css").is_file()
    assert "noc-memory-ui-v146.css" in cache
    assert "noc-memory-ui-v146.js" in cache
    assert cache.index("icon-actions.css") < cache.index("noc-memory-ui-v146.css")
    assert cache.index("icon-actions.js") < cache.index("noc-memory-ui-v146.js")


def test_filters_are_aligned_and_wired() -> None:
    css = (UI / "noc-memory-ui-v146.css").read_text(encoding="utf-8")
    js = (UI / "noc-memory-ui-v146.js").read_text(encoding="utf-8")
    assert "#filter-investigations" not in css or "#view-investigations .filters" in css
    assert "42px" in css
    assert "filter-investigations" in js and "loadInvestigations" in js
    assert "filter-inventory" in js and "loadInventory" in js


def test_playbook_controls_move_to_header_and_editor_is_above_catalog() -> None:
    css = (UI / "noc-memory-ui-v146.css").read_text(encoding="utf-8")
    js = (UI / "noc-memory-ui-v146.js").read_text(encoding="utf-8")
    assert "playbook-manager-toolbar" in js
    assert "playbook-manager-pager" in js
    assert "playbook-modal-head-actions" in js
    assert "#playbook-editor-modal.playbook-modal" in css
    assert "z-index:14050" in css


def test_multi_host_collapses_and_registration_uses_modal() -> None:
    css = (UI / "noc-memory-ui-v146.css").read_text(encoding="utf-8")
    js = (UI / "noc-memory-ui-v146.js").read_text(encoding="utf-8")
    assert ".multi-host-config[hidden]{display:none!important}" in css
    assert "O host principal será o servidor de entrada." in js
    assert "access-monitor-register-modal" in js
    assert "Cadastrar servidor" in js
    assert "window.prompt" not in js


def test_agent_modal_contains_authorized_categories_and_skills() -> None:
    js = (UI / "noc-memory-ui-v146.js").read_text(encoding="utf-8")
    css = (UI / "noc-memory-ui-v146.css").read_text(encoding="utf-8")
    assert "Categorias autorizadas" in js
    assert "cmk-policy-panel" in js
    assert "skills-manager-button" in js
    assert "agent-policy-scope" in css


def test_projects_and_analysis_are_compacted() -> None:
    js = (UI / "noc-memory-ui-v146.js").read_text(encoding="utf-8")
    css = (UI / "noc-memory-ui-v146.css").read_text(encoding="utf-8")
    assert "execution-mode-badge" in js
    assert "project-builder-head" in js
    assert "project-help" in js
    assert "execute.textContent = 'Executar'" in js
    assert "#view-projects #project-generate" in css
