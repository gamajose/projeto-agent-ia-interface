from __future__ import annotations

from types import SimpleNamespace

from app.services.investigation_insights import enrich_investigation_result
from app.services.result_presentation import build_ticket_report_ptbr


class FakePlaybook:
    id = "vpn-monitoramento"
    title = "Diagnóstico de monitoramento pela VPN"
    profiles = ("checkmk",)
    patterns = ("monitoramento", "vpn")

    def score(self, objective: str, profile: str) -> int:
        assert profile == "checkmk"
        assert "monitoramento" in objective.casefold()
        return 84


def _settings() -> SimpleNamespace:
    return SimpleNamespace(agent_max_rounds=5, agent_max_commands=20)


def _result() -> dict:
    return {
        "target": "172.27.232.109",
        "hostname": "2com-monitor",
        "context": "Investigar falha de monitoramento pela VPN",
        "profile": "checkmk",
        "identity": {"hostname": "2com-monitor", "os_name": "Oracle Linux 8"},
        "environment_classification": {"environment": "monitoring"},
        "connection": {
            "mode": "vpn_menu",
            "bastion_host": "10.17.181.1",
            "vpn_index": 1,
            "vpn_ip": "172.27.232.109",
            "client_name": "HOTBEL MONITOR",
            "ssh_port": 22,
            "username": "2com",
            "is_pfsense": False,
            "access_journey": [
                {"step": "bastion", "label": "Monitor 1", "status": "completed", "detail": "Monitor 1 autenticado."},
                {"step": "inventory", "label": "Inventário VPN", "status": "completed", "detail": "HOTBEL MONITOR localizado."},
                {"step": "target_shell", "label": "Shell do alvo", "status": "completed", "detail": "Shell validado."},
            ],
        },
        "inventory": {"saved": True, "hostname": "HOTBEL MONITOR", "ssh_port": 22},
        "playbook": {
            "id": "vpn-monitoramento",
            "title": "Diagnóstico de monitoramento pela VPN",
            "database_learning": {"successful_cases": 2},
        },
        "plans": [
            {
                "hypotheses": ["Rota VPN indisponível", "Serviço Checkmk parado"],
                "tools": [
                    {"tool": "network.routes", "arguments": {}},
                    {"tool": "network.routes", "arguments": {}},
                ],
            }
        ],
        "round_assessments": [
            {
                "hypotheses_confirmed": ["Serviço Checkmk parado"],
                "hypotheses_discarded": ["Rota VPN indisponível"],
                "remaining_questions": ["Confirmar recuperação do sensor após o start."],
            }
        ],
        "evidence": [
            {"status": "executed", "exit_code": 0, "command": "ip route", "stdout": "rota presente"},
            {"status": "failed", "exit_code": 255, "command": "cmk -nv host", "stderr": "comando excedeu o timeout de 60s"},
        ],
        "tool_feedback": {"unavailable": ["tcpdump"]},
        "history": [
            {
                "id": "same-1",
                "created_at": "2026-07-30T10:00:00+00:00",
                "analysis": {"probable_cause": "Serviço Checkmk parado"},
            }
        ],
        "similar_history": [
            {
                "id": "similar-1",
                "created_at": "2026-07-28T10:00:00+00:00",
                "probable_cause": "Serviço Checkmk parado",
            }
        ],
        "analysis": {
            "status": "attention",
            "confidence": 86,
            "summary": "O acesso está funcional e o serviço de monitoramento está parado.",
            "facts": ["A rota VPN está presente."],
            "probable_cause": "Serviço Checkmk parado",
            "conclusion": "A falha está no serviço do destino, não na VPN.",
            "recommendations": ["Validar o serviço e repetir a coleta do sensor."],
            "evidence_map": [
                {"conclusion": "A rota existe", "command": "ip route", "evidence": "rota presente"}
            ],
        },
    }


def test_enriches_result_with_explainability_quality_and_recurrence(monkeypatch) -> None:
    monkeypatch.setattr("app.services.investigation_insights.get_playbook", lambda _playbook_id: FakePlaybook())
    result = _result()

    enrich_investigation_result(result, settings=_settings())

    analysis = result["analysis"]
    assert result["display_target"] == "HOTBEL MONITOR"
    assert analysis["target_context"]["client_name"] == "HOTBEL MONITOR"
    assert analysis["target_context"]["vpn_ip"] == "172.27.232.109"
    assert analysis["access_journey"][-1]["step"] == "target_shell"
    assert analysis["access_journey"][-1]["status"] == "completed"
    assert "Serviço Checkmk parado" in analysis["confirmed_hypotheses"]
    assert "Rota VPN indisponível" in analysis["discarded_hypotheses"]
    assert analysis["hypotheses"] == []
    assert any("tcpdump" in item for item in analysis["missing_information"])
    assert analysis["recurrence"]["total"] == 2
    assert "Serviço Checkmk parado" in analysis["recurrence"]["previous_probable_causes"]
    assert analysis["playbook_match"]["score"] == 84
    assert analysis["playbook_match"]["selected"] is True
    assert analysis["execution_controls"]["duplicate_requests_ignored"] == 1
    assert analysis["execution_controls"]["timeouts"] == 1
    assert 0 <= analysis["quality"]["overall"] <= 100
    assert analysis["next_safe_step"] == "Validar o serviço e repetir a coleta do sensor."
    assert "HOTBEL MONITOR" in analysis["facts"][0]
    assert "shell do alvo" in analysis["explainability"]["where_stopped"].casefold()


def test_ticket_report_separates_facts_hypotheses_and_next_step(monkeypatch) -> None:
    monkeypatch.setattr("app.services.investigation_insights.get_playbook", lambda _playbook_id: FakePlaybook())
    result = _result()
    enrich_investigation_result(result, settings=_settings())

    report = build_ticket_report_ptbr(result["analysis"])

    assert "Alvo: HOTBEL MONITOR" in report
    assert "IP: 172.27.232.109" in report
    assert "Caminho de acesso:" in report
    assert "Fatos comprovados:" in report
    assert "Hipóteses descartadas:" in report
    assert "Evidências ainda necessárias:" in report
    assert "Playbook selecionado:" in report
    assert "Recorrência:" in report
    assert "Próximo passo mais seguro:" in report
