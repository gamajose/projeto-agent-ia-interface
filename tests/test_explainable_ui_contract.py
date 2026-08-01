from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_explainable_analysis_is_exposed_in_result_drawer() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "investigation-flow.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "app" / "ui" / "investigation-flow.css").read_text(encoding="utf-8")

    for marker in (
        "data-explainable-analysis",
        "CAMINHO DE ACESSO",
        "RACIOCÍNIO EXPLICÁVEL",
        "Fatos comprovados",
        "Hipóteses em avaliação",
        "Evidências pendentes",
        "Onde parou?",
        "Causa mais provável",
        "Próximo passo seguro",
        "PLAYBOOK SELECIONADO",
        "MEMÓRIA OPERACIONAL",
        "QUALIDADE",
        "CONTROLE DA EXECUÇÃO",
        "analysis.target_context",
        "analysis.access_journey",
        "analysis.playbook_match",
        "analysis.recurrence",
        "analysis.quality",
        "analysis.execution_controls",
    ):
        assert marker in script

    for selector in (
        ".explainable-analysis",
        ".target-context-card",
        ".access-journey",
        ".reasoning-columns",
        ".explainability-answers",
        ".quality-grid",
        ".control-metrics",
    ):
        assert selector in styles


def test_history_prefers_client_name_from_vpn_inventory() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "investigation-flow.js").read_text(encoding="utf-8")

    assert "context.client_name || item.hostname || item.target" in script
    assert "context.vpn_ip" in script
    assert "item.analysis?.playbook_match?.title" in script
