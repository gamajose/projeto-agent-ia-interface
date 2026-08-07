from __future__ import annotations

from app.services.project_macro_result import build_project_macro_result, project_blueprint


def _plan() -> dict:
    return {
        "groups": [
            {
                "label": "Produção",
                "target": "172.27.233.45",
                "kind": "remote",
                "items": [
                    {
                        "id": "agent-copy",
                        "title": "Copiar o agente Checkmk pelo Monitor 1",
                        "kind": "change",
                        "automated": False,
                        "command": "scp pacote servidor:/tmp/",
                        "purpose": "copiar agente",
                    },
                    {
                        "id": "agent-install",
                        "title": "Instalar o agente Checkmk",
                        "kind": "change",
                        "automated": False,
                        "command": "yum install -y check-mk-agent.rpm",
                        "purpose": "instalar agente",
                    },
                    {
                        "id": "agent-local-validation",
                        "title": "Validar listener e saída local do agente",
                        "kind": "command",
                        "automated": True,
                        "command": "validar agente",
                        "purpose": "validar agente",
                    },
                ],
            }
        ]
    }


def test_existing_checkmk_agent_skips_copy_and_reinstall() -> None:
    blueprint = project_blueprint(_plan(), reference="172.27.233.45")
    result = build_project_macro_result(
        blueprint=blueprint,
        evidence=[
            {
                "step_id": "agent-local-validation",
                "reference": "172.27.233.45",
                "command": "validar agente",
                "exit_code": 0,
                "stdout": "check-mk-agent-2.0.0p25-1.noarch\nLISTEN 0 64 *:6556 *:*\n<<<check_mk>>>",
                "stderr": "",
            }
        ],
        diagnostics={"executed": 1},
        target={"vpn_ip": "172.27.233.45"},
        scenario="linux_prod_std",
        scenario_label="Servidor Linux — Produção/Standby",
    )

    by_id = {item["id"]: item for item in result["checklist"]}
    assert result["facts"]["agent_installed"] is True
    assert by_id["agent-copy"]["status"] == "completed"
    assert by_id["agent-install"]["status"] == "completed"
    assert "nenhuma reinstalação" in by_id["agent-install"]["summary"]
    assert by_id["agent-local-validation"]["status"] == "completed"
    assert "já instalado" in by_id["agent-local-validation"]["summary"]


def test_missing_checkmk_agent_keeps_installation_as_manual_step() -> None:
    blueprint = project_blueprint(_plan(), reference="172.27.233.45")
    result = build_project_macro_result(
        blueprint=blueprint,
        evidence=[
            {
                "step_id": "agent-local-validation",
                "reference": "172.27.233.45",
                "command": "validar agente",
                "exit_code": 0,
                "stdout": "inactive\n",
                "stderr": "",
            }
        ],
        diagnostics={"executed": 1},
        target={"vpn_ip": "172.27.233.45"},
        scenario="linux_prod_std",
        scenario_label="Servidor Linux — Produção/Standby",
    )

    by_id = {item["id"]: item for item in result["checklist"]}
    assert result["facts"]["agent_installed"] is False
    assert by_id["agent-copy"]["status"] == "manual"
    assert by_id["agent-install"]["status"] == "manual"
