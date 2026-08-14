from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_problem_batch_assets_are_loaded_by_versioned_ui() -> None:
    cache = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")

    assert '"noc-problem-batch-v1470.css"' in cache
    assert '"noc-problem-batch-v1470.js"' in cache


def test_problem_batch_ui_exposes_master_skill_and_all_hosts_action() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "noc-problem-batch-v1470.js").read_text(encoding="utf-8")

    assert "NOC Master Skill" in script
    assert "Corrigir por problema" in script
    assert "Arrumar todos" in script
    assert "/ui/api/noc/problem-groups" in script
    assert "/problem-groups/${encodeURIComponent(procedureId)}/run" in script
    assert "Procedimento identificado pelo sensor/erro" in script
    assert "noc-manual-skill" in script
    assert "select.hidden = true" in script


def test_selected_progress_tracks_problem_batch_runs() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "noc-selected-progress-v1465.js").read_text(encoding="utf-8")

    assert "AgentNocSelectedProgress" in script
    assert "problem-groups" in script
    assert "procedureBatchRun" in script


def test_problem_batch_router_is_registered() -> None:
    main = (PROJECT_ROOT / "app" / "web_main.py").read_text(encoding="utf-8")
    router = (PROJECT_ROOT / "app" / "web_noc_batch.py").read_text(encoding="utf-8")

    assert "noc_batch_router" in main
    assert '"/ui/api/noc/problem-groups"' in router
    assert '"/ui/api/noc/problem-groups/{procedure_id}/run"' in router
