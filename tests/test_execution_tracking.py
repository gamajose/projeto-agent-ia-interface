from __future__ import annotations

import time
from unittest.mock import patch

from app.services.progress import report_progress
from app.services.tracked_runner import persist_result_inventory
from app.services.ui_executions import execution_detail, submit_ui_execution


def test_ui_execution_tracks_phases_percentage_and_result() -> None:
    def operation():
        report_progress("ssh_connection", detail="Conectando ao alvo.", percent=36)
        report_progress("ssh_connection", status="completed", detail="SSH validado.", percent=42)
        report_progress("evidence_analysis", detail="Coletando evidências.", percent=55)
        return {"investigation_id": "investigation-1", "analysis": {"status": "healthy"}}

    created = submit_ui_execution(
        operation,
        target="192.0.2.10",
        objective="validar serviço",
        provider="groq",
        model="modelo-a",
        execution_mode="inline",
    )

    deadline = time.monotonic() + 5
    record = created
    while record.get("status") not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        record = execution_detail(created["execution_id"]) or {}

    assert record["status"] == "completed"
    assert record["percent"] == 100
    assert record["result"]["investigation_id"] == "investigation-1"
    stages = [item["stage"] for item in record["phases"]]
    assert "execution_started" in stages
    assert "ssh_connection" in stages
    assert "evidence_analysis" in stages
    assert "completed" in stages
    percentages = [int(item.get("percent") or 0) for item in record["phases"]]
    assert percentages == sorted(percentages)


def test_ui_execution_preserves_failure_and_last_percentage_for_follow_up() -> None:
    def operation():
        report_progress("provider_validation", detail="Validando provedor.", percent=10)
        raise RuntimeError("falha controlada")

    created = submit_ui_execution(
        operation,
        target="192.0.2.11",
        objective="validar falha",
        provider="auto",
        model=None,
        execution_mode="inline",
    )

    deadline = time.monotonic() + 5
    record = created
    while record.get("status") not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        record = execution_detail(created["execution_id"]) or {}

    assert record["status"] == "failed"
    assert "falha controlada" in record["error"]
    assert record["current_phase"]["stage"] == "failed"
    assert record["percent"] >= 10


def test_persist_result_inventory_uses_learning_service_and_updates_analysis() -> None:
    result = {
        "target": "servidor-a",
        "hostname": "srv-a",
        "profile": "linux_generic",
        "identity": {
            "hostname": "srv-a",
            "os_name": "Oracle Linux 8.8",
            "ip_brief": "eth0 UP 10.0.0.10/24\nlo UNKNOWN 127.0.0.1/8",
        },
        "environment_classification": {"environment": "monitoring"},
        "analysis": {"status": "attention"},
    }
    inventory = {
        "saved": True,
        "id": "host-1",
        "vpn_ip": "172.27.232.203",
        "ssh_port": 2222,
        "hostname": "srv-a",
        "environment": "monitoring",
    }

    with patch(
        "app.services.tracked_runner.learn_result_inventory",
        return_value=inventory,
    ) as learn:
        persisted = persist_result_inventory(
            result,
            resolved_host="172.27.232.203",
            ssh_port=2222,
        )

    learn.assert_called_once()
    assert persisted["inventory"] == inventory
    assert persisted["analysis"]["inventory"] == inventory


def test_inventory_failure_does_not_discard_investigation() -> None:
    result = {
        "investigation_id": "investigation-2",
        "target": "192.0.2.12",
        "identity": {"hostname": "srv-b", "os_name": "Linux"},
        "environment_classification": {"environment": "production"},
    }

    with patch(
        "app.services.tracked_runner.learn_result_inventory",
        return_value={"saved": False, "detail": "RuntimeError: db indisponível"},
    ):
        persisted = persist_result_inventory(result, resolved_host="192.0.2.12", ssh_port=22)

    assert persisted["investigation_id"] == "investigation-2"
    assert persisted["inventory"]["saved"] is False
    assert "db indisponível" in persisted["inventory"]["detail"]
