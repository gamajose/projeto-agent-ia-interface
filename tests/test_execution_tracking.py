from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from app.services.progress import report_progress
from app.services.tracked_runner import persist_result_inventory
from app.services.ui_executions import execution_detail, submit_ui_execution


def test_ui_execution_tracks_phases_and_result() -> None:
    def operation():
        report_progress("ssh_connection", detail="Conectando ao alvo.")
        report_progress("ssh_connected", status="completed", detail="SSH validado.")
        report_progress("evidence_analysis", detail="Coletando evidências.")
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
    assert record["result"]["investigation_id"] == "investigation-1"
    stages = [item["stage"] for item in record["phases"]]
    assert "execution_started" in stages
    assert "ssh_connection" in stages
    assert "ssh_connected" in stages
    assert "evidence_analysis" in stages


def test_ui_execution_preserves_failure_for_follow_up() -> None:
    def operation():
        report_progress("provider_validation", detail="Validando provedor.")
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


def test_persist_result_inventory_uses_discovered_identity() -> None:
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
    }

    with patch(
        "app.services.tracked_runner.upsert_host",
        return_value=SimpleNamespace(id="host-1"),
    ) as upsert:
        persisted = persist_result_inventory(
            result,
            resolved_host="172.27.232.203",
            ssh_port=2222,
        )

    upsert.assert_called_once_with(
        host_type="linux_generic",
        vpn_ip="172.27.232.203",
        ssh_port=2222,
        hostname="srv-a",
        os_name="Oracle Linux 8.8",
        environment="monitoring",
        internal_ips=["eth0 UP 10.0.0.10/24", "lo UNKNOWN 127.0.0.1/8"],
    )
    assert persisted["inventory"]["saved"] is True
    assert persisted["inventory"]["hostname"] == "srv-a"


def test_inventory_failure_does_not_discard_investigation() -> None:
    result = {
        "investigation_id": "investigation-2",
        "target": "192.0.2.12",
        "identity": {"hostname": "srv-b", "os_name": "Linux"},
        "environment_classification": {"environment": "production"},
    }

    with patch("app.services.tracked_runner.upsert_host", side_effect=RuntimeError("db indisponível")):
        persisted = persist_result_inventory(result, resolved_host="192.0.2.12", ssh_port=22)

    assert persisted["investigation_id"] == "investigation-2"
    assert persisted["inventory"]["saved"] is False
    assert "db indisponível" in persisted["inventory"]["detail"]
