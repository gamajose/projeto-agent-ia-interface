from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"


def test_refinement_assets_are_published_after_compact_layer() -> None:
    cache = (ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    for name in ("operational-refinement.css", "operational-refinement.js", "playbook-manager-v2.js"):
        assert (UI / name).is_file(), name
        assert name in cache, name
    assert cache.index("compact-operations.css") < cache.index("operational-refinement.css")
    assert cache.index("compact-noc-layout.js") < cache.index("operational-refinement.js")


def test_noc_refinement_compacts_queue_and_moves_controls_to_context() -> None:
    css = (UI / "operational-refinement.css").read_text(encoding="utf-8")
    js = (UI / "operational-refinement.js").read_text(encoding="utf-8")
    assert "compact-queue-tools" in css
    assert "setupIncidentQueue" in js
    assert "setupCheckmkHeading" in js
    assert "setupAgentPolicies" in js
    assert "agent-modal-tools" in js
    assert "setupDiscoveryLayout" in js
    assert "fleet-scope-field" in js


def test_dashboard_storage_metrics_are_safe_and_visible() -> None:
    api = (ROOT / "app" / "web_storage_metrics.py").read_text(encoding="utf-8")
    skill_router = (ROOT / "app" / "web_skill_catalog.py").read_text(encoding="utf-8")
    ui = (UI / "operational-refinement.js").read_text(encoding="utf-8")
    assert '@router.get("/ui/api/observability/storage")' in api
    assert "pg_database_size(current_database())" in api
    assert "pg_stat_user_tables" in api
    assert "used_memory" in api and "dbsize" in api
    assert "storage_metrics_router" in skill_router
    assert "database-overview-grid" in ui
    assert "/ui/api/observability/storage" in ui


def test_skills_can_be_generated_from_playbooks_with_ai() -> None:
    manager = (UI / "noc-skills-manager.js").read_text(encoding="utf-8")
    api = (ROOT / "app" / "web_skill_catalog.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "skill_from_playbook.py").read_text(encoding="utf-8")
    assert "Importar do playbook" in manager
    assert "Analisar com IA" in manager
    assert "/ui/api/noc/skills/from-playbook-preview" in manager
    assert '@router.post("/ui/api/noc/skills/from-playbook-preview")' in api
    assert "generate_json" in service
    assert '"skills"' in service
    assert "playbook_id" in service


def test_playbooks_have_search_pagination_and_full_crud() -> None:
    manager = (UI / "playbook-manager-v2.js").read_text(encoding="utf-8")
    api = (ROOT / "app" / "web_playbooks.py").read_text(encoding="utf-8")
    crud = (ROOT / "app" / "services" / "playbook_crud.py").read_text(encoding="utf-8")
    assert "playbook-manager-search" in manager
    assert "12/página" in manager and "data-prev" in manager and "data-next" in manager
    assert "data-edit-playbook" in manager and "data-delete-playbook" in manager
    assert "method: 'PUT'" in manager and "method: 'DELETE'" in manager
    assert '@router.get("/ui/api/playbooks/manage")' in api
    assert '@router.put("/ui/api/playbooks/{playbook_id}")' in api
    assert '@router.delete("/ui/api/playbooks/{playbook_id}")' in api
    assert "update_playbook_document" in crud and "delete_playbook_document" in crud


def test_inventory_n2_guardrails_and_analysis_modal_are_repositioned() -> None:
    js = (UI / "operational-refinement.js").read_text(encoding="utf-8")
    css = (UI / "operational-refinement.css").read_text(encoding="utf-8")
    assert "setupGuardrails" in js and "settings-guardrails-modal" in js
    assert "setupInventory" in js
    assert "setupN2History" in js and "n2-host-card" in js
    assert "setupAnalysisModal" in js
    assert ".analysis-modal{z-index:12050" in css
    assert "100dvh" in css
