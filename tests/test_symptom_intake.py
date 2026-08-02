from __future__ import annotations

from types import SimpleNamespace

from app.services import symptom_intake
from app.services.symptom_intake import (
    enrich_reasoning_prompt,
    enrich_result_with_symptom,
    parse_reported_symptom,
    use_reported_symptom,
)
from app.services.symptom_reasoning import _cause_only_repeats_symptom


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        agent_recovery_max_rounds=3,
        agent_recovery_max_actions=6,
        agent_recovery_max_diagnostics_per_round=4,
    )


def test_automation_helper_alert_is_starting_symptom() -> None:
    symptom = parse_reported_symptom(
        "O serviço automation-helper está parado no site frj. Investigar o problema."
    )

    assert symptom["reported"] is True
    assert symptom["component"] == "automation-helper"
    assert symptom["reported_state"] == "stopped"
    assert symptom["accepted_as_starting_observation"] is True
    assert "Por que automation-helper" in symptom["investigation_question"]


def test_reasoning_prompt_focuses_on_why_instead_of_rechecking_alert() -> None:
    with use_reported_symptom("Process automation-helper stopped"):
        prompt = enrich_reasoning_prompt("PLANEJE A INVESTIGAÇÃO", "planning_round_1")

    assert "Não desperdice rodadas apenas para provar novamente" in prompt
    assert "O sintoma não é a causa raiz" in prompt
    assert "Investigue por que" in prompt
    assert "PLANEJE A INVESTIGAÇÃO" in prompt


def test_repeating_stopped_state_is_not_accepted_as_root_cause(monkeypatch) -> None:
    monkeypatch.setattr(symptom_intake, "get_settings", _settings)
    monkeypatch.setattr(symptom_intake, "_same_as_symptom", _cause_only_repeats_symptom)
    symptom = parse_reported_symptom("O automation-helper está parado")
    result = {
        "target": "172.27.1.10",
        "hostname": "monitor-jose",
        "playbook": {"allowed_corrections": ["checkmk.recover_omd_service"]},
        "environment_classification": {"environment": "monitoring"},
        "analysis": {
            "probable_cause": "O serviço automation-helper está parado.",
            "critic": {"verdict": "accept"},
            "incident_intelligence": {
                "conclusion_validation": {"verdict": "supported"}
            },
            "evidence_map": [
                {"conclusion": "processo parado", "evidence": "omd status: stopped"}
            ],
            "proposed_actions": [],
        },
    }

    enrich_result_with_symptom(result, symptom)

    root_cause = result["analysis"]["root_cause"]
    assert root_cause["status"] == "unknown"
    assert root_cause["statement"] == ""
    assert root_cause["symptom_was_not_used_as_cause"] is False
    assert any(
        "falta explicar o mecanismo" in item
        for item in result["analysis"]["missing_information"]
    )


def test_upstream_failure_can_be_confirmed_as_root_cause(monkeypatch) -> None:
    monkeypatch.setattr(symptom_intake, "get_settings", _settings)
    monkeypatch.setattr(symptom_intake, "_same_as_symptom", _cause_only_repeats_symptom)
    symptom = parse_reported_symptom("Process automation-helper stopped")
    result = {
        "target": "172.27.1.10",
        "hostname": "monitor-jose",
        "playbook": {"allowed_corrections": ["checkmk.recover_omd_service"]},
        "environment_classification": {"environment": "monitoring"},
        "analysis": {
            "probable_cause": (
                "O filesystem do site atingiu 100% e impediu a criação do arquivo "
                "temporário usado na inicialização do automation-helper."
            ),
            "critic": {"verdict": "accept"},
            "incident_intelligence": {
                "conclusion_validation": {"verdict": "supported"}
            },
            "evidence_map": [
                {
                    "conclusion": "filesystem esgotado",
                    "command": "df -hP /omd/sites/frj",
                    "evidence": "/omd/sites/frj 100%",
                },
                {
                    "conclusion": "falha ligada à falta de espaço",
                    "command": "journalctl",
                    "evidence": "No space left on device",
                },
            ],
            "proposed_actions": [
                {"tool": "checkmk.recover_omd_service", "status": "proposed"}
            ],
        },
    }

    enrich_result_with_symptom(result, symptom)

    root_cause = result["analysis"]["root_cause"]
    assert root_cause["status"] == "confirmed"
    assert "filesystem" in root_cause["statement"].casefold()
    assert root_cause["causal_chain"][0]["type"] == "root_cause"
    assert root_cause["causal_chain"][-1]["type"] == "reported_symptom"
    scope = result["analysis"]["recovery_scope"]
    assert scope["allowed_correction_tools"] == ["checkmk.recover_omd_service"]
    assert scope["database_access"] is False
    assert scope["server_reboot"] is False
    assert scope["container_lifecycle"] is False
