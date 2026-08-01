from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_incident_panel_feedback_and_playbook_review_are_available() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "correction-flow.js").read_text(encoding="utf-8")
    routes = (PROJECT_ROOT / "app" / "web_incidents.py").read_text(encoding="utf-8")
    web_main = (PROJECT_ROOT / "app" / "web_main.py").read_text(encoding="utf-8")

    required_script = (
        "INTELIGÊNCIA DE INCIDENTES",
        "Validação contrária da conclusão",
        "Mapa de dependências",
        "Validade das evidências",
        "O diagnóstico da IA foi confirmado?",
        "data-incident-feedback",
        "/feedback",
        "/playbook-draft",
        "/playbook-drafts/",
        "data-review-draft",
        "Aprovar e ativar",
    )
    for item in required_script:
        assert item in script

    for endpoint in (
        '/ui/api/investigations/{investigation_id}/feedback',
        '/ui/api/investigations/{investigation_id}/playbook-draft',
        '/ui/api/playbook-drafts/{draft_id}',
        '/ui/api/playbook-drafts/{draft_id}/review',
    ):
        assert endpoint in routes

    assert "incidents_router" in web_main
    assert "ui_cache_router" in web_main


def test_ui_html_is_served_with_dynamic_asset_version_and_no_cache() -> None:
    cache_module = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")

    assert "importlib.metadata.version" in cache_module
    assert '"Cache-Control": "no-store, max-age=0"' in cache_module
    assert "X-Agent-UI-Version" in cache_module
    assert "re.sub" in cache_module
