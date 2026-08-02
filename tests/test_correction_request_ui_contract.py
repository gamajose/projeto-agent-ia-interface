from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_recovery_ui_offers_correction_after_analysis() -> None:
    source = (ROOT / "app" / "ui" / "recovery-flow.js").read_text(encoding="utf-8")

    required = (
        "Deseja solicitar a correção?",
        "Solicitar correção",
        "Verificando impacto e reinícios...",
        "Executar sem reiniciar a máquina",
        "Executar ações e preparar reinício manual",
        "Já reiniciei; executar nova varredura",
        "/prepare-correction",
        "/recheck",
        "Varredura e pós-validação",
    )
    for item in required:
        assert item in source


def test_recovery_ui_removes_old_direct_approval_box() -> None:
    source = (ROOT / "app" / "ui" / "recovery-flow.js").read_text(encoding="utf-8")

    assert 'content.querySelector(".approval-box")?.remove()' in source
    assert "Nada será executado antes da sua confirmação" in source
