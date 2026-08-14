from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "app" / "ui" / "noc-metric-modals.js"


def test_metric_cards_open_incident_detail_modal() -> None:
    content = SCRIPT.read_text(encoding="utf-8")
    assert "data-metric-incident-id" in content
    assert "showIncidentDetail" in content
    assert "/ui/api/noc/incidents/" in content
    assert "Por que precisa de você" in content
    assert "Causa provável" in content
    assert "Conclusão da IA" in content
    assert "Evidências da investigação" in content
    assert "Linha do tempo" in content


def test_metric_detail_supports_all_summary_groups() -> None:
    content = SCRIPT.read_text(encoding="utf-8")
    assert "'IA trabalhando': ['queued', 'investigating', 'watching']" in content
    assert "'Precisa de você': ['awaiting_approval', 'needs_attention']" in content
    assert "'Resolvidos hoje': ['resolved']" in content
    assert "Revalidar Checkmk" in content
    assert "Abrir investigação completa" in content
