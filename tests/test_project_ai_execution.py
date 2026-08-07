from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import dynamic_agent as engine
from app.services import intelligent_agent
from app.services.playbooks import Playbook
from app.services.project_playbook_instrumentation import install_project_playbook_instrumentation
from app.web_projects import _plan_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _playbook(playbook_id: str) -> Playbook:
    return Playbook(
        id=playbook_id,
        title="teste",
        priority=1,
        profiles=("any",),
        patterns=(),
        steps=({"command": "hostname", "purpose": "identificar host"},),
        allowed_corrections=(),
        validation_tools=(),
        source="test",
    )


def test_project_ui_starts_execution_instead_of_rendering_copyable_commands() -> None:
    source = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert 'api("/ui/api/projects/start"' in source
    assert 'api("/ui/api/projects/plan"' not in source
    assert "Copiar comandos" not in source
    assert "project-copy-command" not in source
    assert "Você não precisa copiar nenhum comando" in source
    assert "Executar validação com IA" in source


def test_project_ui_normalizes_async_response_before_reading_array_lengths() -> None:
    source = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert "const asArray = (value) => (Array.isArray(value) ? value : []);" in source
    assert "normalizeExecutionResponse" in source
    assert "jobs: asArray(response.jobs)" in source
    assert "executions: asArray(response.executions)" in source
    assert "errors: asArray(response.errors)" in source
    assert "response.jobs.length" in source
    assert "response.executions.length" in source
    assert "response.jobs || []" not in source


def test_project_plan_snapshot_does_not_send_command_groups_to_ui() -> None:
    snapshot = _plan_snapshot(
        {
            "plan_id": "p1",
            "scenario": "linux_prod_std",
            "scenario_label": "Produção",
            "target": {"vpn_ip": "172.27.232.10"},
            "discovery": {"target": {"reachable": True}},
            "warnings": [],
            "summary": {"automatic_read_only_steps": 5},
            "safety": {"automatic_scope": "read_only"},
            "ticket_macro": "macro",
            "groups": [
                {
                    "items": [
                        {"command": "hostname"},
                        {"command": "dmidecode -t1"},
                    ]
                }
            ],
        }
    )

    assert snapshot["target"]["vpn_ip"] == "172.27.232.10"
    assert "groups" not in snapshot
    assert "command" not in repr(snapshot)


def test_project_playbooks_execute_initial_steps_even_when_general_playbooks_are_advisory(monkeypatch) -> None:
    monkeypatch.setattr(
        intelligent_agent,
        "get_settings",
        lambda: SimpleNamespace(agent_playbook_advisory_only=True),
    )
    install_project_playbook_instrumentation()

    token = intelligent_agent._INTELLIGENT_SESSION.set(True)
    try:
        project_steps = engine.render_steps(_playbook("project-linux-prod-std"), {"target": "172.27.232.10"})
        advisory_steps = engine.render_steps(_playbook("generic-unrelated-playbook"), {"target": "172.27.232.10"})
    finally:
        intelligent_agent._INTELLIGENT_SESSION.reset(token)

    assert project_steps == [{"command": "hostname", "purpose": "identificar host"}]
    assert advisory_steps == []
