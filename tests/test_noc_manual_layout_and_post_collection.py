from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from app.core.policies import EnvironmentType
from app.services.approved_execution import _requires_checkmk_post_collection
from app.services.checkmk_post_correction import (
    build_post_correction_collection_command,
    collect_target_from_monitor,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeMonitor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, EnvironmentType, int]] = []

    def run_sudo(self, command, environment, timeout=60, approved=False):
        self.calls.append((command, environment, timeout))
        return SimpleNamespace(
            exit_code=0,
            stdout=(
                "MATCH MODE=docker CONTAINER=checkmk-sma-25 SITE=sma HOST=sma-dbstandby\n"
                "MATCH_COUNT=1\n"
                "COLLECTION_STATUS=SUCCESS\n"
            ),
            stderr="",
        )


class FakeSiteScopedExecutor:
    def __init__(self, parent) -> None:
        self.parent = parent


def test_manual_modal_removes_intro_and_restores_compact_checkbox_alignment() -> None:
    script = (PROJECT_ROOT / "app/ui/noc-manual-modal-v1468.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "app/ui/noc-manual-modal-v1468.css").read_text(encoding="utf-8")

    assert "Escolha exatamente o cliente, host e problema" not in script
    assert "ui.modal('noc-manual-execution-modal', 'Execução manual', '')" in script
    assert 'grid-template-columns:17px minmax(0,1fr)!important' in css
    assert 'input[type="checkbox"]{width:15px!important' in css
    assert '.noc-manual-list{height:250px' in css
    assert 'width:min(1320px,96vw)' in css


def test_systemd_socket_playbook_contains_target_monitor_contract() -> None:
    payload = yaml.safe_load(
        (PROJECT_ROOT / "config/playbooks/checkmk-systemd-socket-summary.yml").read_text(encoding="utf-8")
    )

    assert "TARGET_HOST" in payload["summary"]
    assert "MONITORING_HOST" in payload["summary"]
    assert "TARGET_HOST == MONITORING_HOST" in "\n".join(payload["safety_rules"])
    assert "cmk --flush" in "\n".join(payload["validation_notes"])
    assert "cmk --debug -vvn" in "\n".join(payload["validation_notes"])
    assert payload["source"]["filename"] == "Playbook de Correção Checkmk.pdf"
    assert "MONITORING_HOST ou relacionamento equivalente no inventário" in payload["required_inputs"]


def test_agent_corrections_require_post_collection() -> None:
    assert _requires_checkmk_post_collection(
        [{"tool": "checkmk.resolve_legacy_socket_conflict", "arguments": {}}]
    )
    assert _requires_checkmk_post_collection(
        [
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "check-mk-agent.socket", "action": "restart"},
            }
        ]
    )
    assert not _requires_checkmk_post_collection(
        [
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "snmpd.service", "action": "restart"},
            }
        ]
    )


def test_post_collection_searches_docker_and_native_and_requires_unique_site() -> None:
    command = build_post_correction_collection_command("sma-dbstandby")

    assert "docker ps" in command
    assert "omd sites --bare" in command
    assert "MATCH_COUNT=$matches" in command
    assert "matches\" -ne 1" in command
    assert "cmk -D sma-dbstandby" in command
    assert "cmk --flush sma-dbstandby" in command
    assert "cmk --debug -vvn sma-dbstandby" in command
    assert "COLLECTION_STATUS=SUCCESS" in command


def test_post_collection_runs_on_monitor_parent() -> None:
    monitor = FakeMonitor()
    executor = FakeSiteScopedExecutor(monitor)

    result = collect_target_from_monitor(executor, "sma-dbstandby")

    assert result["status"] == "validated"
    assert result["same_site_only"] is True
    assert result["monitoring_context"] is True
    assert monitor.calls
    command, environment, timeout = monitor.calls[0]
    assert environment == EnvironmentType.MONITORING
    assert timeout == 360
    assert "cmk --debug -vvn sma-dbstandby" in command
