from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import noc_selected_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _RedisStub:
    def __init__(self, pending: list[str] | None = None, jobs: list[str] | None = None) -> None:
        self.pending = pending or []
        self.jobs = jobs or []

    def lrange(self, key: str, _start: int, _end: int):
        if key.endswith("autonomy:runs:pending"):
            return list(self.pending)
        return list(self.jobs)


def _settings():
    return SimpleNamespace(
        redis_url="redis://unused/0",
        noc_incident_prefix="agent-ia:noc",
        agent_queue_name="agent-ia:jobs",
    )


def test_pending_manual_run_exposes_position(monkeypatch) -> None:
    monkeypatch.setattr(noc_selected_status, "_redis", lambda _settings: _RedisStub(pending=["other", "run-1"]))
    run = {"id": "run-1", "status": "queued", "result": None}

    enriched = noc_selected_status.enrich_selected_run(run, settings=_settings())

    assert enriched["queue_position"] == 2
    assert enriched["progress"]["total"] == 0


def test_single_manual_job_exposes_live_progress(monkeypatch) -> None:
    monkeypatch.setattr(noc_selected_status, "_redis", lambda _settings: _RedisStub())
    monkeypatch.setattr(
        noc_selected_status,
        "get_job",
        lambda _job_id, *, settings: {
            "status": "running",
            "percent": 42,
            "current_phase": {"stage": "ssh", "detail": "Coletando evidências no host."},
        },
    )
    run = {
        "id": "run-1",
        "status": "completed",
        "result": {"jobs": [{"job_id": "job-1", "host": "srv01", "service": "Filesystem /"}]},
    }

    enriched = noc_selected_status.enrich_selected_run(run, settings=_settings())

    assert enriched["status"] == "running"
    assert enriched["progress"]["total"] == 1
    assert enriched["progress"]["percent"] == 42
    assert enriched["jobs"][0]["detail"] == "Coletando evidências no host."


def test_multiple_manual_jobs_expose_queue_and_completion(monkeypatch) -> None:
    queued_raw = '{"job_id":"job-2"}'
    monkeypatch.setattr(noc_selected_status, "_redis", lambda _settings: _RedisStub(jobs=[queued_raw]))

    def fake_job(job_id: str, *, settings):
        if job_id == "job-1":
            return {"status": "completed", "percent": 100}
        return {"status": "queued", "percent": 0, "current_phase": {"detail": "Aguardando worker."}}

    monkeypatch.setattr(noc_selected_status, "get_job", fake_job)
    run = {
        "id": "run-2",
        "status": "completed",
        "result": {
            "jobs": [
                {"job_id": "job-1", "host": "srv01", "service": "CPU"},
                {"job_id": "job-2", "host": "srv02", "service": "Memory"},
            ]
        },
    }

    enriched = noc_selected_status.enrich_selected_run(run, settings=_settings())

    assert enriched["status"] == "queued"
    assert enriched["progress"]["total"] == 2
    assert enriched["progress"]["completed"] == 1
    assert enriched["jobs"][1]["queue_position"] == 1


def test_manual_runner_and_ui_assets_are_wired() -> None:
    runner = (PROJECT_ROOT / "app" / "services" / "noc_selected_runner.py").read_text(encoding="utf-8")
    worker = (PROJECT_ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    cache = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    ui = (PROJECT_ROOT / "app" / "ui" / "noc-selected-progress-v1465.js").read_text(encoding="utf-8")

    assert "start_selected_run_processor_background" in worker
    assert "_prioritize_jobs" in runner
    assert "scope_matches_problem" in runner
    assert "noc-selected-progress-v1465.js" in cache
    assert "noc-selected-progress-v1465.css" in cache
    assert "FILA MANUAL" in ui
    assert "AJUSTE MANUAL" in ui
    assert "queue_position" in ui
