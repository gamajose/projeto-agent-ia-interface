from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import noc_problem_batch as batch
from app.services import noc_problem_batch_dispatch as dispatch


def _recent_problem() -> dict:
    return {
        "problem_key": "site-a|service|srv01|Systemd Socket Summary",
        "site_id": "site-a",
        "client_alias": "EMPRESA A",
        "alias": "EMPRESA A",
        "host": "srv01",
        "host_address": "10.1.1.10",
        "service": "Systemd Socket Summary",
        "state_name": "CRIT",
        "output": "Failed: 1, check_mk.socket",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }


def test_recent_persisted_snapshot_skips_new_global_collection(monkeypatch) -> None:
    settings = SimpleNamespace()
    problem = _recent_problem()
    captured: dict = {}

    monkeypatch.setattr(batch, "_active_batch_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch, "_persisted_active_problems", lambda: [problem])
    monkeypatch.setattr(
        batch,
        "request_procedure_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("não deveria forçar nova fotografia")),
    )

    def fake_request_selected_run(**kwargs):
        captured.update(kwargs)
        return {"id": "run-fast", "status": "queued", "scope": {}}

    def fake_save(run, *, procedure_id, batch, snapshot_completed_at, settings):
        return {**run, "batch": batch, "scope": {"batch_snapshot_completed_at": snapshot_completed_at}}

    monkeypatch.setattr(dispatch, "request_selected_run", fake_request_selected_run)
    monkeypatch.setattr(batch, "_save_batch_context", fake_save)

    result = dispatch.request_procedure_batch(
        "checkmk-systemd-socket-summary",
        sites=["site-a"],
        operator="José",
        settings=settings,
    )

    assert result["id"] == "run-fast"
    assert result["batch"]["snapshot_source"] == "recent_persisted"
    assert result["batch"]["reused"] is False
    assert captured["problem_keys"] == [problem["problem_key"]]


def test_stale_snapshot_falls_back_to_conservative_refresh(monkeypatch) -> None:
    stale = _recent_problem()
    stale["last_seen_at"] = "2020-01-01T00:00:00+00:00"
    settings = SimpleNamespace()

    monkeypatch.setattr(batch, "_active_batch_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch, "_persisted_active_problems", lambda: [stale])
    monkeypatch.setattr(
        batch,
        "request_procedure_batch",
        lambda *args, **kwargs: {"id": "run-refreshed", "status": "queued", "batch": {"snapshot_source": "live"}},
    )

    result = dispatch.request_procedure_batch(
        "checkmk-systemd-socket-summary",
        sites=["site-a"],
        settings=settings,
    )

    assert result["id"] == "run-refreshed"
    assert result["batch"]["snapshot_source"] == "live"
