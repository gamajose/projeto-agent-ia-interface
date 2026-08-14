from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from app.core.policies import EnvironmentType
from app.services import noc_autonomy_control
from app.services import site_scoped_execution
from app.services import site_scoped_runner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.queues: dict[str, list[str]] = {}

    def setex(self, key, _ttl, value):
        self.values[str(key)] = str(value)

    def rpush(self, key, value):
        self.queues.setdefault(str(key), []).append(str(value))


class FakeParent:
    def __init__(self) -> None:
        self.host = "10.17.181.1"
        self.port = 22
        self.connection_metadata = {"transport": "vpn_menu"}
        self.connected = False
        self.closed = False
        self.commands: list[tuple[str, str]] = []

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True

    def run(self, command, environment, approved=False, timeout=60):
        self.commands.append(("run", command))
        return SimpleNamespace(command=command, exit_code=0, stdout="ok", stderr="")

    def run_sudo(self, command, environment, approved=False, timeout=60):
        self.commands.append(("sudo", command))
        return SimpleNamespace(command=command, exit_code=0, stdout="ok", stderr="")


class FakeNested:
    instances: list["FakeNested"] = []

    def __init__(self, parent, *, host, port, username, password, route, connect_timeout, strict_host_key_checking):
        self.parent = parent
        self.host = host
        self.port = port
        self.route = route
        self.connection_metadata = {"transport": "nested", "host": host}
        self.connected = False
        self.closed = False
        self.commands: list[tuple[str, str]] = []
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True

    def run(self, command, environment, approved=False, timeout=60):
        self.commands.append(("run", command))
        return SimpleNamespace(command=command, exit_code=0, stdout="ok", stderr="")

    def run_sudo(self, command, environment, approved=False, timeout=60):
        self.commands.append(("sudo", command))
        return SimpleNamespace(command=command, exit_code=0, stdout="ok", stderr="")


class FakeBudget:
    def snapshot(self):
        return {"commands": 2}


class FakeSelection:
    provider = "fake"
    model = "fake-model"

    def as_dict(self):
        return {"provider": self.provider, "model": self.model}


def test_manual_run_persists_optional_playbook_and_skill(monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(noc_autonomy_control, "_redis", lambda _settings: redis)
    settings = SimpleNamespace(noc_incident_prefix="agent-ia:noc")

    run = noc_autonomy_control.request_selected_run(
        sites=["sma"],
        hosts=["sma-dbstandby"],
        problem_keys=["sma|sma-dbstandby|Systemd Socket Summary"],
        playbook_id="checkmk-systemd-socket-summary",
        skill_id="checkmk-systemd-socket-summary",
        operator="Jose",
        settings=settings,
    )

    assert run["scope"]["playbook_id"] == "checkmk-systemd-socket-summary"
    assert run["scope"]["skill_id"] == "checkmk-systemd-socket-summary"
    assert run["manual_options"] == {
        "playbook_id": "checkmk-systemd-socket-summary",
        "skill_id": "checkmk-systemd-socket-summary",
    }
    assert redis.queues


def test_site_scoped_runner_hands_monitoring_correction_back_to_same_client(monkeypatch) -> None:
    FakeNested.instances.clear()
    parent = FakeParent()
    persisted: list[tuple[str, dict]] = []
    calls: list[tuple[object, str, EnvironmentType]] = []

    monkeypatch.setattr(site_scoped_runner, "VPNMenuSSHExecutor", FakeParent)
    monkeypatch.setattr(site_scoped_runner, "NestedSSHExecutor", FakeNested)
    monkeypatch.setattr(site_scoped_runner, "resolve_target", lambda *args, **kwargs: SimpleNamespace(host="10.17.181.1", port=22))
    monkeypatch.setattr(site_scoped_runner, "build_executor", lambda *args, **kwargs: parent)
    monkeypatch.setattr(site_scoped_runner, "get_secret", lambda *args, **kwargs: "secret")
    monkeypatch.setattr(site_scoped_runner, "resolve_automatic_provider", lambda _settings: FakeSelection())
    monkeypatch.setattr(site_scoped_runner, "use_provider", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(site_scoped_runner, "use_playbook", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(site_scoped_runner, "use_investigation_budget", lambda: nullcontext(FakeBudget()))
    monkeypatch.setattr(site_scoped_runner, "report_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(site_scoped_runner, "stamp_evidence_timing", lambda *args, **kwargs: None)
    monkeypatch.setattr(site_scoped_runner, "enrich_investigation_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(site_scoped_runner, "enrich_incident_intelligence", lambda *args, **kwargs: None)
    monkeypatch.setattr(site_scoped_runner, "finalize_result_presentation", lambda *args, **kwargs: None)
    monkeypatch.setattr(site_scoped_runner, "increment", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        site_scoped_runner,
        "update_investigation_analysis",
        lambda investigation_id, analysis: persisted.append((str(investigation_id), dict(analysis))) or True,
    )

    def fake_investigation(*, executor, target, context, environment, mode, approve):
        calls.append((executor, target, environment))
        if isinstance(executor, FakeNested):
            return {
                "investigation_id": "11111111-1111-1111-1111-111111111111",
                "hostname": "sma-dbstandby",
                "evidence": [{"tool": "system.basics", "status": "executed", "stdout": "host evidence"}],
                "analysis": {
                    "status": "attention",
                    "confidence": 94,
                    "summary": "Falha depende do OMD no servidor de monitoramento.",
                    "proposed_actions": [
                        {
                            "tool": "checkmk.recover_omd_service",
                            "arguments": {"container": "checkmk-sma-25", "site": "sma", "service": "xinetd", "action": "restart"},
                            "status": "proposed",
                        }
                    ],
                },
            }
        return {
            "investigation_id": "22222222-2222-2222-2222-222222222222",
            "hostname": "sma-monitor",
            "evidence": [{"tool": "checkmk.find_omd_service", "status": "executed"}],
            "analysis": {
                "status": "attention",
                "confidence": 96,
                "summary": "OMD confirmado no monitor.",
                "proposed_actions": [
                    {
                        "tool": "checkmk.recover_omd_service",
                        "arguments": {"container": "checkmk-sma-25", "site": "sma", "service": "xinetd", "action": "restart"},
                        "status": "proposed",
                    }
                ],
            },
        }

    monkeypatch.setattr(site_scoped_runner, "run_dynamic_investigation", fake_investigation)
    settings = SimpleNamespace(
        ai_provider="auto",
        ssh_default_user="2com",
        ssh_default_password="secret",
        ssh_connect_timeout=15,
        ssh_strict_host_key_checking=False,
    )

    result = site_scoped_runner.run_site_scoped_target(
        "10.17.181.1",
        "Resolver alerta do standby",
        site_id="sma",
        client_alias="SUPERMERCADO MANAR LTDA",
        host_name="sma-dbstandby",
        internal_target="10.1.1.223",
        target_strategy="internal_ssh",
        environment=EnvironmentType.STANDBY,
        provider_name="auto",
        settings=settings,
    )

    assert len(calls) == 2
    assert isinstance(calls[0][0], FakeNested)
    assert calls[0][1] == "10.1.1.223"
    assert calls[1][0] is parent
    assert calls[1][1] == "10.17.181.1"
    assert result["investigation_id"] == "22222222-2222-2222-2222-222222222222"
    assert result["site_scope"]["site_id"] == "sma"
    assert result["site_scope"]["correction_context"] == "monitoring_entry"
    assert result["site_scope"]["cross_host"] is True
    assert result["cross_host_flow"]["alert_host"] == "sma-dbstandby"
    assert result["cross_host_flow"]["monitoring_followup"] is True
    assert any(item[0] == result["investigation_id"] for item in persisted)


def test_approved_site_route_uses_nested_host_but_keeps_omd_on_monitor(monkeypatch) -> None:
    FakeNested.instances.clear()
    parent = FakeParent()
    monkeypatch.setattr(site_scoped_execution, "VPNMenuSSHExecutor", FakeParent)
    monkeypatch.setattr(site_scoped_execution, "NestedSSHExecutor", FakeNested)
    monkeypatch.setattr(site_scoped_execution, "resolve_target", lambda *args, **kwargs: SimpleNamespace(host="10.17.181.1", port=22))
    monkeypatch.setattr(site_scoped_execution, "build_executor", lambda *args, **kwargs: parent)
    monkeypatch.setattr(site_scoped_execution, "get_secret", lambda *args, **kwargs: "secret")
    settings = SimpleNamespace(
        ssh_default_user="2com",
        ssh_default_password="secret",
        ssh_connect_timeout=15,
        ssh_strict_host_key_checking=False,
    )
    analysis = {
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
        }
    }

    route = site_scoped_execution.build_approved_execution_route(
        {"target": "10.1.1.223"},
        analysis,
        environment=EnvironmentType.STANDBY,
        approved_ssh_port=22,
        settings=settings,
    )
    route.executor.connect()
    nested = FakeNested.instances[-1]
    route.executor.run_sudo("systemctl restart check-mk-agent.socket", EnvironmentType.STANDBY, approved=True)
    route.executor.run_sudo("docker exec checkmk-sma-25 su - sma -c 'omd restart xinetd'", EnvironmentType.STANDBY, approved=True)
    route.executor.close()

    assert route.site_scoped is True
    assert route.context == "affected_host"
    assert any("check-mk-agent.socket" in command for _, command in nested.commands)
    assert any("omd restart xinetd" in command for _, command in parent.commands)
    assert parent.closed is True
    assert nested.closed is True


def test_manual_ui_is_separate_from_automatic_switch_and_sends_optional_knowledge() -> None:
    script = (PROJECT_ROOT / "app/ui/noc-manual-modal-v1468.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "app/ui/noc-manual-modal-v1468.css").read_text(encoding="utf-8")
    cache = (PROJECT_ROOT / "app/web_ui_cache.py").read_text(encoding="utf-8")
    approved = (PROJECT_ROOT / "app/services/approved_execution.py").read_text(encoding="utf-8")

    assert "noc-manual-button" in script
    assert "Execução manual" in script
    assert "Automático ligado" in script
    assert "playbook_id:" in script
    assert "skill_id:" in script
    assert "#noc-selected-scope{display:none!important}" in css
    assert "noc-manual-modal-v1468.js" in cache
    assert "noc-manual-modal-v1468.css" in cache
    assert "build_approved_execution_route" in approved
