from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import dynamic_agent as engine
from app.services import intelligent_agent
from app.services.playbooks import Playbook
from app.services.project_macro_result import build_project_macro_result, project_blueprint
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


def test_project_ui_executes_macro_without_investigation_language() -> None:
    source = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert 'api("/ui/api/projects/start"' in source
    assert 'api("/ui/api/projects/plan"' not in source
    assert "Copiar comandos" not in source
    assert "project-copy-command" not in source
    assert "RESULTADO DA MACRO" in source
    assert "Validações do projeto" in source
    assert "Não inicia investigação de causa raiz" in source
    assert "Causa provável" not in source
    assert "Revisar proposta e aprovar" not in source


def test_project_ui_normalizes_async_response_before_reading_array_lengths() -> None:
    source = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert "const asArray = (value) => (Array.isArray(value) ? value : []);" in source
    assert "normalizeExecutionResponse" in source
    assert "jobs: asArray(response.jobs)" in source
    assert "executions: asArray(response.executions)" in source
    assert "errors: asArray(response.errors)" in source


def test_project_plan_snapshot_exposes_checklist_without_commands() -> None:
    plan = {
        "plan_id": "p1",
        "scenario": "linux_prod_std",
        "scenario_label": "Produção",
        "target": {"vpn_ip": "172.27.232.10"},
        "warnings": [],
        "summary": {"automatic_read_only_steps": 1},
        "safety": {"automatic_scope": "read_only"},
        "ticket_macro": "macro",
        "groups": [
            {
                "label": "Produção",
                "target": "172.27.232.10",
                "kind": "remote",
                "items": [
                    {
                        "id": "hostname",
                        "title": "Identificar host",
                        "kind": "command",
                        "automated": True,
                        "command": "hostname",
                        "purpose": "identificar",
                        "evidence": "print",
                    }
                ],
            }
        ],
    }

    snapshot = _plan_snapshot(plan)

    assert snapshot["target"]["vpn_ip"] == "172.27.232.10"
    assert snapshot["checklist"][0]["title"] == "Identificar host"
    assert snapshot["checklist"][0]["status"] == "pending"
    assert snapshot["checklist"][0]["kind"] == "command"
    assert "groups" not in snapshot
    assert all("command" not in item for item in snapshot["checklist"])


def test_macro_result_turns_command_output_into_ticket_evidence() -> None:
    plan = {
        "groups": [
            {
                "label": "Produção",
                "target": "172.27.233.45",
                "kind": "remote",
                "items": [
                    {
                        "id": "os-version",
                        "title": "Validar versão do sistema operacional",
                        "kind": "command",
                        "automated": True,
                        "command": "cat /etc/*-release",
                        "purpose": "validar SO",
                        "evidence": "tirar print",
                    },
                    {
                        "id": "ind-panel",
                        "title": "Validar painel",
                        "kind": "manual",
                        "automated": False,
                        "command": "",
                        "purpose": "validar painel",
                        "evidence": "tirar print",
                    },
                ],
            }
        ]
    }
    blueprint = project_blueprint(plan, reference="172.27.233.45")
    result = build_project_macro_result(
        blueprint=blueprint,
        evidence=[
            {
                "step_id": "os-version",
                "reference": "172.27.233.45",
                "command": "cat /etc/*-release",
                "exit_code": 0,
                "stdout": 'NAME="Oracle Linux Server"\nVERSION_ID="8.10"\nPRETTY_NAME="Oracle Linux Server 8.10"',
                "stderr": "",
            }
        ],
        diagnostics={"executed": 1},
        target={"vpn_ip": "172.27.233.45"},
        scenario="linux_prod_std",
        scenario_label="Servidor Linux — Produção/Standby",
        ticket_macro="macro",
    )

    assert result["kind"] == "project_validation"
    assert result["facts"]["os_name"] == "Oracle Linux Server 8.10"
    assert result["checklist"][0]["status"] == "completed"
    assert result["checklist"][0]["evidence"]["stdout"].startswith("NAME=")
    assert result["checklist"][1]["status"] == "manual"
    assert result["summary"] == {"total": 2, "completed": 1, "failed": 0, "manual": 1, "pending": 0, "automatic": 1}


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
