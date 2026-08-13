from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"


def test_compact_operational_assets_exist_and_are_published() -> None:
    required = (
        "compact-operations.css",
        "compact-operations.js",
        "compact-noc-layout.js",
        "noc-metric-modals.js",
        "noc-queue-controls.js",
        "noc-queue-pagination.js",
        "noc-skills-manager.js",
        "noc-resolved-detail.js",
        "inventory-pagination.js",
    )
    cache = (ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    for name in required:
        assert (UI / name).is_file(), name
        assert name in cache, name


def test_n2_is_compacted_into_history_modal_and_two_primary_steps() -> None:
    source = (UI / "compact-operations.js").read_text(encoding="utf-8")
    assert ".n2-page-head" in source
    assert "n2-history-compact" in source
    assert "compact-n2-history" in source
    assert "#n2-new-document" in source
    assert ".n2-run-card" in source
    assert ".n2-host-card" in source
    assert "reviewStep.textContent = '3'" in source


def test_noc_controls_are_moved_to_modals() -> None:
    source = (UI / "compact-noc-layout.js").read_text(encoding="utf-8")
    assert "compact-agent-modal" in source
    assert "Correções automáticas" in source
    assert "Descoberta de rede" in source
    assert "compact-filter-modal" in source
    assert "#cmk-sync" in source
    assert "#cmk-poll" in source
    assert "#fleet-refresh" in source


def test_noc_queue_and_inventory_have_pagination_and_ordering() -> None:
    controls = (UI / "noc-queue-controls.js").read_text(encoding="utf-8")
    queue = (UI / "noc-queue-pagination.js").read_text(encoding="utf-8")
    inventory = (UI / "inventory-pagination.js").read_text(encoding="utf-8")
    assert "noc-queue-filter" in controls
    assert "noc-queue-order" in controls
    assert "noc-queue-pager" in queue
    assert "data-prev" in queue and "data-next" in queue
    assert "inventory-order" in inventory
    assert "inventory-pager" in inventory
    assert "24/página" in inventory


def test_resolved_incident_does_not_keep_processing_placeholder() -> None:
    source = (UI / "noc-resolved-detail.js").read_text(encoding="utf-8")
    assert "recuperação antes de existir evidência suficiente" in source
    assert "Incidente normalizado automaticamente" in source


def test_skill_manager_has_runtime_crud_and_is_registered() -> None:
    manager = (UI / "noc-skills-manager.js").read_text(encoding="utf-8")
    api = (ROOT / "app" / "web_skill_catalog.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "web_main.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "noc_skills.py").read_text(encoding="utf-8")
    assert "/ui/api/noc/skills/catalog" in manager
    assert "Nova skill" in manager and "Editar" in manager and "Remover" in manager
    assert '@router.post("/ui/api/noc/skills/catalog")' in api
    assert '@router.delete("/ui/api/noc/skills/catalog/{skill_id}")' in api
    assert "skill_catalog_router" in main
    assert "noc-skills.yml" in service
    assert "os.replace" in service
    assert "0o600" in service
