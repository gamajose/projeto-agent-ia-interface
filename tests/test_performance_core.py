from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.core.policies import EnvironmentType
from app.services.execution_store import ExecutionStore
from app.services.investigation_budget import InvestigationBudget, InvestigationBudgetExceeded
from app.services.metrics import increment, observe, render_prometheus
from app.services.multi_host_triage import triage_host
from app.services.performance_config import get_performance_config
from app.services.ssh import CommandResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_execution_store_persists_records_and_streams_in_memory() -> None:
    store = ExecutionStore()
    store.enabled = False
    store.save("abc", {"execution_id": "abc", "status": "running"})
    assert store.get("abc")["status"] == "running"

    first = store.append_event("abc", {"stage": "command_started"})
    second = store.append_event("abc", {"stage": "command_completed"})
    assert first == "1"
    assert second == "2"
    rows, cursor = store.read_events("abc", "0", block_milliseconds=1)
    assert [item[1]["stage"] for item in rows] == ["command_started", "command_completed"]
    assert cursor == "2"


def test_investigation_budget_limits_commands_ai_and_deep_dives() -> None:
    config = replace(
        get_performance_config(),
        max_total_commands=2,
        max_total_ai_calls=1,
        max_deep_dive_hosts=1,
        max_investigation_seconds=60,
        max_host_seconds=60,
    )
    budget = InvestigationBudget(config)
    assert budget.reserve_command("host-a", 30) == 30
    assert budget.reserve_command("host-a", 30) == 30
    try:
        budget.reserve_command("host-a", 30)
    except InvestigationBudgetExceeded as exc:
        assert "2 comandos" in str(exc)
    else:
        raise AssertionError("o limite de comandos deveria bloquear a terceira reserva")
    budget.reserve_ai_call("gemini")
    try:
        budget.reserve_ai_call("gemini")
    except InvestigationBudgetExceeded:
        pass
    else:
        raise AssertionError("o limite de IA deveria bloquear a segunda chamada")
    assert budget.allow_deep_dive("host-a")
    assert not budget.allow_deep_dive("host-b")


def test_metrics_render_prometheus_contract() -> None:
    increment("agent_test_metric", labels={"status": "ok"})
    observe("agent_test_duration_seconds", 1.5, labels={"kind": "unit"})
    output = render_prometheus()
    assert 'agent_test_metric_total{status="ok"}' in output
    assert 'agent_test_duration_seconds_count{kind="unit"}' in output
    assert 'agent_test_duration_seconds_sum{kind="unit"}' in output


class _TriageExecutor:
    host = "10.45.1.24"
    route = {"role": "monitoring"}

    def run(self, command, environment, approved=False, timeout=60):
        assert environment == EnvironmentType.MONITORING
        assert not approved
        assert timeout == 30
        return CommandResult(
            command,
            0,
            "\n".join(
                [
                    "HOSTNAME=monitor-jose",
                    "UPTIME=up 10 days",
                    "KERNEL=5.15.0",
                    "LOAD=0.10 0.20 0.30",
                    "MEMORY=2000/8000 swap=0/2048",
                    "FAILED_UNITS=1",
                    "FILESYSTEMS_BEGIN",
                    "92%|/var",
                    "FILESYSTEMS_END",
                    "UNHEALTHY_BEGIN",
                    "checkmk-jose|Up 2 days (unhealthy)",
                    "UNHEALTHY_END",
                    "OMD_BEGIN",
                    "jose",
                    "OMD_END",
                ]
            ),
            "",
        )


def test_multi_host_triage_prioritizes_relevant_host() -> None:
    result = triage_host(
        _TriageExecutor(),
        objective="Investigar alerta Checkmk no standby",
        environment=EnvironmentType.MONITORING,
        label="Monitoramento José",
        timeout=30,
    )
    assert result["status"] == "attention"
    assert result["score"] >= 60
    assert result["triage"]["failed_units"] == 1
    assert result["triage"]["unhealthy_containers"]


def test_sse_and_persistent_nested_ssh_contracts() -> None:
    web = (PROJECT_ROOT / "app" / "web_executions.py").read_text(encoding="utf-8")
    tracker = (PROJECT_ROOT / "app" / "ui" / "execution-tracker.js").read_text(encoding="utf-8")
    nested = (PROJECT_ROOT / "app" / "services" / "nested_ssh.py").read_text(encoding="utf-8")
    queries = (PROJECT_ROOT / "app" / "services" / "ui_queries.py").read_text(encoding="utf-8")

    assert 'text/event-stream' in web
    assert '/events' in web
    assert 'new EventSource' in tracker
    assert 'schedulePoll' in tracker
    assert 'ControlMaster=yes' in nested
    assert 'ControlPersist=' in nested
    assert '-O exit' in nested
    assert 'InvestigationORM.evidence' not in queries.split('def list_investigations', 1)[1].split('def list_hosts', 1)[0]
