from __future__ import annotations

import time
from unittest.mock import patch

from app.services.cancellation import raise_if_cancelled
from app.services.progress import report_progress
from app.services.tracked_runner import persist_result_inventory
from app.services.ui_executions import (
    execution_detail,
    request_execution_cancel,
    submit_ui_execution,
)


def _wait_terminal(execution_id: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    record = execution_detail(execution_id) or {}
    while record.get("status") not in {"completed", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(0.02)
        record = execution_detail(execution_id) or {}
    return record


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

    record = _wait_terminal(created["execution_id"])

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


def test_ui_execution_preserves_live_command_events() -> None:
    def operation():
        report_progress(
            "command_started",
            detail="Executando uname -a",
            command_id="command-1",
            command="uname -a",
            percent=55,
        )
        report_progress(
            "command_output",
            detail="Recebendo saída",
            command_id="command-1",
            command="uname -a",
            stdout_tail="Linux servidor 6.8",
            percent=65,
        )
        report_progress(
            "command_completed",
            status="completed",
            detail="Comando concluído",
            command_id="command-1",
            command="uname -a",
            exit_code=0,
            stdout_tail="Linux servidor 6.8",
            percent=75,
        )
        return {"investigation_id": "investigation-live"}

    created = submit_ui_execution(
        operation,
        target="192.0.2.21",
        objective="acompanhar comandos",
        provider="ollama",
        model="llama3.2:1b",
        execution_mode="inline",
    )
    record = _wait_terminal(created["execution_id"])

    command_events = [item for item in record["events"] if item.get("command_id") == "command-1"]
    assert [item["stage"] for item in command_events] == [
        "command_started",
        "command_output",
        "command_completed",
    ]
    assert command_events[-1]["stdout_tail"] == "Linux servidor 6.8"
    assert command_events[-1]["exit_code"] == 0


def test_ui_execution_can_be_cancelled_cooperatively() -> None:
    def operation():
        report_progress("evidence_analysis", detail="Coleta longa em andamento.", percent=55)
        for _ in range(300):
            raise_if_cancelled()
            time.sleep(0.01)
        return {"investigation_id": "should-not-complete"}

    created = submit_ui_execution(
        operation,
        target="192.0.2.22",
        objective="cancelar coleta",
        provider="auto",
        model=None,
        execution_mode="inline",
    )

    deadline = time.monotonic() + 2
    record = execution_detail(created["execution_id"]) or {}
    while record.get("percent", 0) < 55 and time.monotonic() < deadline:
        time.sleep(0.01)
        record = execution_detail(created["execution_id"]) or {}

    requested = request_execution_cancel(created["execution_id"])
    assert requested is not None
    assert requested["status"] == "cancelling"

    record = _wait_terminal(created["execution_id"])
    assert record["status"] == "cancelled"
    assert record["result"] is None
    assert record["current_phase"]["status"] == "cancelled"
    assert "cancelada" in record["current_phase"]["detail"].lower()


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

    record = _wait_terminal(created["execution_id"])

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
