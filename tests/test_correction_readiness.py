from __future__ import annotations

from app.services.correction_readiness import assess_correction_readiness


def _investigation(text: str, environment: str = "monitoring") -> dict:
    return {
        "target": "192.0.2.10",
        "environment": environment,
        "objective": text,
        "evidence": [],
        "analysis": {
            "summary": text,
            "recommendations": [],
            "facts": [],
        },
    }


def test_readiness_does_not_invent_machine_restart() -> None:
    result = assess_correction_readiness(
        _investigation("O xinetd está parado e deve ser iniciado."),
        [
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "xinetd", "action": "start"},
            }
        ],
    )

    assert result["host_restart"]["status"] == "not_required"
    assert result["host_restart"]["decision_required"] is False
    assert result["automatic_correction_allowed"] is True


def test_readiness_reports_service_restart_separately() -> None:
    result = assess_correction_readiness(
        _investigation("O serviço precisa ser recuperado."),
        [
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "snmpd", "action": "restart"},
            }
        ],
    )

    assert result["service_restart"]["required"] is True
    assert result["service_restart"]["items"][0]["target"] == "snmpd"
    assert result["host_restart"]["status"] == "not_required"


def test_readiness_requires_human_decision_when_evidence_mentions_reboot() -> None:
    item = _investigation(
        "Foi identificado kernel instalado diferente do kernel em uso. É necessário reiniciar o servidor para concluir a atualização.",
        environment="training",
    )
    item["evidence"] = [
        {
            "purpose": "comparar kernel",
            "stdout": "kernel instalado 6.8.0; kernel em uso 5.15.0; reboot required",
            "stderr": "",
        }
    ]

    result = assess_correction_readiness(item, [])

    assert result["host_restart"]["status"] == "required"
    assert result["host_restart"]["decision_required"] is True
    assert result["host_restart"]["automatic_execution"] is False
    assert result["host_restart"]["evidence"]


def test_production_still_receives_plan_but_not_automatic_correction() -> None:
    result = assess_correction_readiness(
        _investigation("O xinetd está parado.", environment="production"),
        [
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "xinetd", "action": "start"},
            }
        ],
    )

    assert result["automatic_correction_allowed"] is False
    assert "Produção" in result["policy_message"]
