from __future__ import annotations

import json
from pathlib import Path

from app.services.adaptive_reasoning import enrich_adaptive_prompt


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app" / "ui" / "adaptive-analysis.js").read_text(encoding="utf-8")
CACHE = (ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")


def test_planning_prompt_receives_dynamic_hypothesis_state() -> None:
    payload = {
        "objective": "Site de monitoramento indisponível e automation-helper parado",
        "profile": "checkmk",
        "runtime_context": {"services": ["automation-helper"], "binaries": ["omd"]},
        "evidence": [
            {
                "tool": "checkmk.site.status",
                "command": "omd status frj",
                "status": "executed",
                "exit_code": 0,
                "stdout": "automation-helper stopped\nOverall state partially running",
                "stderr": "",
                "normalized": {},
            }
        ],
        "round_assessments": [],
        "similar_history": [],
    }
    prompt = "REGRAS\n\nENTRADA:\n" + json.dumps(payload, ensure_ascii=False)

    enriched = enrich_adaptive_prompt(prompt, "planning_round_2")

    assert "MOTOR ADAPTATIVO DE HIPÓTESES" in enriched
    assert "ESTADO ADAPTATIVO ATUAL" in enriched
    assert "checkmk_internal_component_failure" in enriched
    assert "não apresente percentuais concorrentes" in enriched


def test_mission_prompt_is_not_modified() -> None:
    prompt = "interprete esta missão"
    assert enrich_adaptive_prompt(prompt, "mission_interpretation") == prompt


def test_adaptive_ui_uses_operational_states_instead_of_percentages() -> None:
    required = (
        "ANÁLISE ADAPTATIVA",
        "Árvore de hipóteses",
        "Fingerprint do ambiente",
        "Próximos testes de maior valor",
        "Grafo de dependências",
        "Agrupamento de alertas",
        "Memória validada",
        "Causa confirmada",
        "Hipótese forte",
        "Em teste",
        "Descartada",
    )
    for text in required:
        assert text in SCRIPT
    assert "item.score" not in SCRIPT
    assert "${item.score}%" not in SCRIPT


def test_versioned_ui_injects_adaptive_asset_after_existing_assets() -> None:
    assert "adaptive-analysis.js" in CACHE
    assert "_inject_adaptive_assets" in CACHE
    assert "content = _inject_adaptive_assets(content)" in CACHE
