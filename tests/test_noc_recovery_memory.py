from __future__ import annotations

from types import SimpleNamespace

from app.services import noc_history_hooks, noc_worker_hooks


def test_incident_lookup_includes_recent_resolved_rows(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_list(**kwargs):
        calls.append(kwargs)
        return {"items": [{"id": "incident-1", "job_id": "job-1", "status": "resolved"}]}

    monkeypatch.setattr(noc_worker_hooks, "list_noc_incidents", fake_list)
    found = noc_worker_hooks._incident_for_job("job-1", SimpleNamespace())

    assert found and found["id"] == "incident-1"
    assert calls[0]["open_only"] is False


def test_resolved_incident_keeps_analysis_but_cannot_correct_late(monkeypatch) -> None:
    monkeypatch.setattr(noc_worker_hooks, "job_runtime_authorization", lambda metadata, settings: (True, "ok"))
    result = {
        "approval_token": "token-que-nao-pode-ser-usado",
        "analysis": {"probable_cause": "socket do agente indisponível", "confidence": 94},
        "evidence": [{"tool": "system.basics", "status": "executed"}],
    }

    safe = noc_worker_hooks._safe_postprocess_result(
        {"metadata": {}},
        result,
        settings=SimpleNamespace(),
        incident={"status": "resolved", "resolution_source": "checkmk_recovery"},
    )

    assert safe["approval_token"] is None
    assert safe["recovered_before_investigation_completed"] is True
    assert safe["analysis"]["probable_cause"] == "socket do agente indisponível"
    assert safe["runtime_autonomy"]["allowed"] is False


def test_history_records_cause_even_when_checkmk_recovered(monkeypatch) -> None:
    recorded: dict = {}
    incident = {
        "id": "incident-1",
        "status": "resolved",
        "probable_cause": "check-mk-agent.socket estava parado",
        "conclusion": "serviço normalizou antes da correção",
        "confidence": 92,
        "resolution_source": "checkmk_recovery",
        "investigation_id": "investigation-1",
        "autonomy": {"eligible": False, "reason": "já normalizado"},
    }
    monkeypatch.setattr(noc_history_hooks, "handle_worker_result", lambda result, settings: incident)

    def fake_record(row, *, status, reason, metadata):
        recorded.update({"row": row, "status": status, "reason": reason, "metadata": metadata})

    monkeypatch.setattr(noc_history_hooks, "record_incident_history", fake_record)
    noc_history_hooks.handle_worker_result_with_history(
        {"job_id": "job-1", "status": "completed", "result": {}},
        settings=SimpleNamespace(),
    )

    assert recorded["status"] == "resolved"
    assert "check-mk-agent.socket estava parado" in recorded["reason"]
    assert recorded["metadata"]["investigation_id"] == "investigation-1"
    assert recorded["metadata"]["confidence"] == 92
