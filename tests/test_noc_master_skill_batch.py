from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import noc_deterministic_skill, noc_problem_batch
from app.services.noc_skills import noc_master_skill, reload_noc_skills, select_noc_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _socket_problem(host: str, key: str, site: str = "sma") -> dict:
    return {
        "problem_key": key,
        "site_id": site,
        "alias": "CLIENTE",
        "host": host,
        "host_address": "10.1.1.223",
        "service": "Systemd Socket Summary",
        "state_name": "CRIT",
        "output": "Total: 13, Disabled: 1, Failed: 1, 1 socket failed (check_mk)",
    }


def test_only_one_physical_noc_skill_file_exists() -> None:
    files = sorted(path.name for path in (PROJECT_ROOT / "config" / "skills").glob("*.yml"))
    assert files == ["noc-master.yml"]


def test_master_skill_exposes_internal_procedures_without_competing_files() -> None:
    procedures = reload_noc_skills()
    ids = {item.id for item in procedures}
    master = noc_master_skill()

    assert master["id"] == "noc-master"
    assert master["procedure_count"] == len(procedures)
    assert "checkmk-systemd-socket-summary" in ids
    assert "linux-systemd-socket-summary" in ids
    assert "linux-filesystem" in ids
    assert "linux-memory-pressure" in ids
    assert "network-link" in ids
    assert "oracle-backup" in ids
    assert "snmp-bmc" in ids
    assert "checkmk-runtime" in ids
    assert all(item.get("master_skill_id") == "noc-master" for item in master["procedures"])


def test_socket_alert_routes_to_master_skill_procedure() -> None:
    selected = select_noc_skill(_socket_problem("sma-dbstandby", "p1"))

    assert selected["master_skill_id"] == "noc-master"
    assert selected["procedure_id"] == "checkmk-systemd-socket-summary"
    assert selected["id"] == "checkmk-systemd-socket-summary"


def test_problem_groups_collect_all_hosts_with_same_problem() -> None:
    problems = [
        _socket_problem("srv01", "p1", "site-a"),
        _socket_problem("srv02", "p2", "site-b"),
        {
            "problem_key": "p3",
            "site_id": "site-a",
            "host": "srv03",
            "host_address": "10.0.0.3",
            "service": "Filesystem /",
            "state_name": "CRIT",
            "output": "Used: 96%",
        },
    ]

    groups = noc_problem_batch.group_problems_by_procedure(problems)
    by_id = {item["procedure_id"]: item for item in groups}

    socket = by_id["checkmk-systemd-socket-summary"]
    assert socket["problem_count"] == 2
    assert socket["host_count"] == 2
    assert socket["site_count"] == 2
    assert socket["problem_keys"] == ["p1", "p2"]
    assert by_id["linux-filesystem"]["problem_count"] == 1


def test_request_procedure_batch_refreshes_snapshot_and_queues_all_matching_problem_keys(monkeypatch) -> None:
    snapshot = {
        "status": "completed",
        "problems": [
            _socket_problem("srv01", "p1", "site-a"),
            _socket_problem("srv02", "p2", "site-b"),
            {
                "problem_key": "p3",
                "site_id": "site-a",
                "host": "srv03",
                "service": "Memory",
                "state_name": "CRIT",
                "output": "virtual memory warn",
            },
        ],
    }
    monkeypatch.setattr(noc_problem_batch, "collect_checkmk_operational_snapshot", lambda settings=None: snapshot)
    monkeypatch.setattr(noc_problem_batch, "_active_batch_run", lambda *args, **kwargs: None)
    captured: dict = {}

    def fake_request_selected_run(**kwargs):
        captured.update(kwargs)
        return {"id": "run-1", "status": "queued", "scope": {}}

    def fake_save(run, *, procedure_id, batch, snapshot_completed_at, settings):
        return {**run, "batch": batch, "scope": {"batch_procedure_id": procedure_id}}

    monkeypatch.setattr(noc_problem_batch, "request_selected_run", fake_request_selected_run)
    monkeypatch.setattr(noc_problem_batch, "_save_batch_context", fake_save)

    result = noc_problem_batch.request_procedure_batch(
        "checkmk-systemd-socket-summary",
        operator="José",
        settings=SimpleNamespace(),
    )

    assert captured["problem_keys"] == ["p1", "p2"]
    assert captured["skill_id"] == "checkmk-systemd-socket-summary"
    assert captured["operator"] == "José"
    assert result["batch"]["problem_count"] == 2
    assert result["batch"]["host_count"] == 2
    assert result["batch"]["procedure_id"] == "checkmk-systemd-socket-summary"


class _Executor:
    def __init__(self) -> None:
        self.parent = self
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True


class _Route:
    def __init__(self) -> None:
        self.executor = _Executor()
        self.site_scoped = True
        self.context = "affected_host"
        self.metadata = {"site_id": "sma", "same_site_only": True}


def _deterministic_result() -> dict:
    return {
        "target": "10.1.1.223",
        "environment_classification": {"environment": "standby", "confidence": 0},
        "analysis": {
            "confidence": 0,
            "site_scope": {
                "isolated": True,
                "same_site_only": True,
                "site_id": "sma",
                "entry_address": "10.17.181.1",
                "host_name": "sma-dbstandby",
                "internal_address": "10.1.1.223",
                "target_strategy": "internal_ssh",
                "correction_context": "affected_host",
            },
        },
    }


def test_socket_procedure_skips_local_changes_when_host_is_already_correct(monkeypatch) -> None:
    route = _Route()
    tool_calls: list[str] = []
    read_calls: list[str] = []
    collection: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        noc_deterministic_skill,
        "build_approved_execution_route",
        lambda *args, **kwargs: route,
    )

    def fake_execute(_executor, _environment, name, _arguments, *, approved=False):
        tool_calls.append(name)
        assert approved is False
        return {"tool": name, "status": "executed", "exit_code": 0}

    monkeypatch.setattr(noc_deterministic_skill, "execute_tool", fake_execute)

    def fake_read(_executor, _environment, _command, *, purpose, timeout=45):
        read_calls.append(purpose)
        if "estado local já corrigido" in purpose:
            return {
                "tool": "checkmk.deterministic_validation",
                "status": "validated",
                "exit_code": 0,
                "stdout": "LOCAL_STATE=ALREADY_CORRECT\n",
                "stderr": "",
            }
        return {
            "tool": "checkmk.deterministic_validation",
            "status": "validated",
            "exit_code": 0,
            "stdout": "LEGACY_SOCKET_FAILED=no\n",
            "stderr": "",
        }

    monkeypatch.setattr(noc_deterministic_skill, "_read_check", fake_read)

    def fake_collect(_executor, target, address=None):
        collection.append((target, address))
        return {
            "stage": "checkmk_post_correction_collection",
            "target_host": target,
            "target_address": address,
            "status": "validated",
            "exit_code": 0,
        }

    monkeypatch.setattr(noc_deterministic_skill, "collect_target_from_monitor", fake_collect)

    settings = SimpleNamespace(
        noc_self_heal_tools="checkmk.resolve_legacy_socket_conflict,systemd.recover_unit",
        ssh_default_user="2com",
        ssh_default_password="",
        ssh_connect_timeout=15,
        ssh_strict_host_key_checking=False,
    )
    incident = {
        "site": "sma",
        "host": "sma-dbstandby",
        "host_address": "10.1.1.223",
        "service": "Systemd Socket Summary",
        "current_state": "CRIT",
        "last_output": "Total: 13, Disabled: 1, Failed: 1, 1 socket failed (check_mk)",
        "environment": "standby",
    }

    execution = noc_deterministic_skill.run_deterministic_skill_correction(
        incident,
        _deterministic_result(),
        settings=settings,
    )

    assert execution is not None
    assert execution["status"] == "validated"
    assert execution["local_already_correct"] is True
    assert tool_calls == ["checkmk.inspect_agent_socket"]
    assert collection == [("sma-dbstandby", "10.1.1.223")]
    assert any("estado local já corrigido" in value for value in read_calls)
    assert any("systemctl --failed" in value for value in read_calls)
    assert route.executor.connected is True
    assert route.executor.closed is True
