from __future__ import annotations

from types import SimpleNamespace

from app.services import noc_problem_batch


def _socket_problem(host: str, key: str, site: str, alias: str) -> dict:
    return {
        "problem_key": key,
        "site_id": site,
        "client_alias": alias,
        "alias": alias,
        "host": host,
        "host_address": "10.10.10.10",
        "service": "Systemd Socket Summary",
        "state_name": "CRIT",
        "output": "Failed: 1, check_mk.socket",
        "automation_status": "detected",
    }


def test_problem_groups_use_last_completed_snapshot_when_collector_is_busy(monkeypatch) -> None:
    problems = [_socket_problem("srv01", "p1", "site-a", "EMPRESA A")]
    monkeypatch.setattr(
        noc_problem_batch,
        "collect_checkmk_operational_snapshot",
        lambda settings=None: {"status": "busy", "problems": []},
    )
    monkeypatch.setattr(noc_problem_batch, "_persisted_active_problems", lambda: problems)
    monkeypatch.setattr(
        noc_problem_batch,
        "checkmk_operational_overview",
        lambda **kwargs: {
            "state": {
                "running": True,
                "last_completed_at": "2026-08-17T11:30:00+00:00",
                "sites_ok": 395,
                "sites_failed": 15,
                "hosts_seen": 727,
            }
        },
    )

    result = noc_problem_batch.current_problem_groups(settings=SimpleNamespace())

    assert result["status"] == "completed"
    assert result["busy"] is True
    assert result["source"] == "persisted_while_busy"
    assert result["problem_count"] == 1
    assert result["groups"][0]["host_count"] == 1
    assert "última fotografia concluída" in result["warning"]


def test_batch_waits_for_running_snapshot_and_uses_just_persisted_state(monkeypatch) -> None:
    problems = [
        _socket_problem("srv01", "p1", "site-a", "EMPRESA A"),
        _socket_problem("srv02", "p2", "site-b", "EMPRESA B"),
    ]
    monkeypatch.setattr(
        noc_problem_batch,
        "collect_checkmk_operational_snapshot",
        lambda settings=None: {"status": "busy", "problems": []},
    )
    monkeypatch.setattr(noc_problem_batch, "_persisted_active_problems", lambda: problems)
    monkeypatch.setattr(
        noc_problem_batch,
        "checkmk_operational_overview",
        lambda **kwargs: {
            "state": {
                "running": False,
                "last_completed_at": "2026-08-17T11:31:00+00:00",
                "sites_ok": 395,
                "sites_failed": 15,
                "hosts_seen": 727,
            }
        },
    )
    captured: dict = {}

    def fake_request_selected_run(**kwargs):
        captured.update(kwargs)
        return {"id": "run-busy", "status": "queued", "scope": {}}

    monkeypatch.setattr(noc_problem_batch, "request_selected_run", fake_request_selected_run)

    result = noc_problem_batch.request_procedure_batch(
        "checkmk-systemd-socket-summary",
        operator="José",
        settings=SimpleNamespace(),
    )

    assert captured["problem_keys"] == ["p1", "p2"]
    assert result["batch"]["snapshot_source"] == "persisted_after_busy"
    assert result["batch"]["host_count"] == 2


def test_problem_group_detail_groups_alerts_by_company_and_host(monkeypatch) -> None:
    problems = [
        _socket_problem("srv01", "p1", "site-a", "EMPRESA A"),
        {
            **_socket_problem("srv01", "p2", "site-a", "EMPRESA A"),
            "service": "Systemd Socket Summary extra",
        },
        _socket_problem("srv02", "p3", "site-b", "EMPRESA B"),
    ]
    monkeypatch.setattr(noc_problem_batch, "_persisted_active_problems", lambda: problems)
    monkeypatch.setattr(
        noc_problem_batch,
        "checkmk_operational_overview",
        lambda **kwargs: {"state": {"running": False, "last_completed_at": "2026-08-17T11:31:00+00:00"}},
    )

    result = noc_problem_batch.problem_group_detail(
        "checkmk-systemd-socket-summary",
        settings=SimpleNamespace(),
    )

    assert result["host_count"] == 2
    assert result["site_count"] == 2
    assert result["problem_count"] == 3
    assert result["members"][0]["client_alias"] == "EMPRESA A"
    assert result["members"][0]["host"] == "srv01"
    assert result["members"][0]["alert_count"] == 2
    assert len(result["members"][0]["alerts"]) == 2
