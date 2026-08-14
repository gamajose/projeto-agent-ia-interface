from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import noc_deterministic_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeExecutor:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.parent = self

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True


class FakeRoute:
    def __init__(self) -> None:
        self.executor = FakeExecutor()
        self.site_scoped = True
        self.context = "affected_host"
        self.metadata = {
            "site_id": "sma",
            "entry_address": "10.17.181.1",
            "internal_address": "10.1.1.223",
            "same_site_only": True,
        }


def _incident() -> dict:
    return {
        "site": "sma",
        "host": "sma-dbstandby",
        "host_address": "10.1.1.223",
        "service": "Systemd Socket Summary",
        "current_state": "CRIT",
        "last_output": "Total: 13, Disabled: 1, Failed: 1, 1 socket failed (check_mk)",
        "environment": "standby",
    }


def _result() -> dict:
    return {
        "target": "10.1.1.223",
        "environment_classification": {"environment": "standby", "confidence": 0},
        "analysis": {
            "confidence": 0,
            "site_scope": {
                "isolated": True,
                "same_site_only": True,
                "site_id": "sma",
                "client_alias": "SUPERMERCADO MANAR LTDA",
                "entry_address": "10.17.181.1",
                "host_name": "sma-dbstandby",
                "internal_address": "10.1.1.223",
                "target_strategy": "internal_ssh",
                "correction_context": "affected_host",
            },
        },
    }


def test_systemd_socket_summary_is_known_deterministic_skill() -> None:
    skill = noc_deterministic_skill.deterministic_skill_for_incident(_incident())

    assert skill is not None
    assert skill["id"] == "checkmk-systemd-socket-summary"
    assert skill["deterministic"]["tools"] == [
        {"tool": "checkmk.resolve_legacy_socket_conflict", "arguments": {}}
    ]
    assert skill["deterministic"]["checkmk_post_collection"] is True


def test_provider_failure_reasons_can_fall_back_to_deterministic_skill() -> None:
    assert noc_deterministic_skill.is_ai_dependency_failure(
        "confiança da análise abaixo do mínimo para correção"
    )
    assert noc_deterministic_skill.is_ai_dependency_failure(
        "segunda IA não aprovou a correção"
    )
    assert not noc_deterministic_skill.is_ai_dependency_failure(
        "ambiente unknown não permite correção"
    )


def test_deterministic_skill_executes_structured_tool_and_post_collection(monkeypatch) -> None:
    route = FakeRoute()
    calls: list[str] = []

    monkeypatch.setattr(
        noc_deterministic_skill,
        "build_approved_execution_route",
        lambda *args, **kwargs: route,
    )

    def fake_execute(_executor, _environment, name, _arguments, *, approved=False):
        calls.append(name)
        if name == "checkmk.inspect_agent_socket":
            return {"tool": name, "status": "executed", "exit_code": 0}
        assert approved is True
        return {"tool": name, "status": "validated", "exit_code": 0}

    monkeypatch.setattr(noc_deterministic_skill, "execute_tool", fake_execute)
    monkeypatch.setattr(
        noc_deterministic_skill,
        "collect_target_from_monitor",
        lambda executor, target: {
            "stage": "checkmk_post_correction_collection",
            "target_host": target,
            "status": "validated",
            "exit_code": 0,
        },
    )
    settings = SimpleNamespace(
        noc_self_heal_tools="checkmk.resolve_legacy_socket_conflict,systemd.recover_unit",
        ssh_default_user="2com",
        ssh_default_password="",
        ssh_connect_timeout=15,
        ssh_strict_host_key_checking=False,
    )

    execution = noc_deterministic_skill.run_deterministic_skill_correction(
        _incident(),
        _result(),
        settings=settings,
    )

    assert execution is not None
    assert execution["status"] == "validated"
    assert execution["state"] == "deterministic_skill_validated"
    assert calls == ["checkmk.inspect_agent_socket", "checkmk.resolve_legacy_socket_conflict"]
    assert execution["results"][-1]["target_host"] == "sma-dbstandby"
    assert route.executor.connected is True
    assert route.executor.closed is True


def test_manual_sensor_list_uses_site_detail_as_source_of_truth() -> None:
    script = (PROJECT_ROOT / "app/ui/noc-manual-modal-v1468.js").read_text(encoding="utf-8")

    assert "Array.isArray(detail?.problems)" in script
    assert "detail.problems" in script
    assert "loadSiteDetail(siteId, true)" in script
    assert "pruneProblemSelection" in script
