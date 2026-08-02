from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "ui" / "recovery-flow.js").read_text(encoding="utf-8")


def test_recovery_asset_is_loaded_after_correction_flow() -> None:
    assert "recovery-flow.js?v=1.29.0" in INDEX
    assert INDEX.index("correction-flow.js") < INDEX.index("recovery-flow.js")


def test_result_separates_alert_symptom_from_root_cause() -> None:
    required = (
        "O alerta é o ponto de partida, não a conclusão",
        "Sintoma recebido",
        "Causa raiz",
        "Cadeia causal",
        "Critérios para considerar resolvido",
        "Envelope da recuperação",
    )
    for text in required:
        assert text in SCRIPT


def test_recovery_ui_supports_adaptive_blockers_and_same_incident_approval() -> None:
    assert "Novo bloqueio investigado" in SCRIPT
    assert "Corrigindo, observando e replanejando" in SCRIPT
    assert "next_approval_token" in SCRIPT
    assert "Aprovar próximo passo seguro" in SCRIPT
    assert "/approve" in SCRIPT
    assert "state.currentInvestigationId" in SCRIPT


def test_ui_does_not_offer_unsafe_recovery_capabilities() -> None:
    assert "Banco: <b>bloqueado</b>" in SCRIPT
    assert "Reinício da máquina: <b>manual</b>" in SCRIPT
    assert "Container: <b>bloqueado</b>" in SCRIPT
    assert "automatic_execution" in (ROOT / "app" / "services" / "correction_readiness.py").read_text(encoding="utf-8")
    assert "rm -rf" not in SCRIPT
    assert "docker restart" not in SCRIPT
    assert "systemctl reboot" not in SCRIPT
