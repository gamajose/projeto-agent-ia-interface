from pathlib import Path

import yaml

from app.core.settings import Settings
from app.services.playbook_editor import save_playbook


def test_save_playbook_persists_review_metadata(tmp_path, monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        postgres_dsn="sqlite+pysqlite:///:memory:",
        agent_playbook_dir=str(tmp_path / "playbooks"),
    )
    monkeypatch.setattr("app.services.playbooks.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.playbook_editor.describe_tools",
        lambda: [{"name": "system.basics", "description": "Básico", "correction": False}],
    )

    save_playbook(
        playbook_id="checkmk-importado",
        title="Diagnóstico Checkmk importado",
        priority=20,
        profiles=["checkmk"],
        patterns=["Missing monitoring data"],
        steps_yaml="- tool: system.basics\n  arguments: {}\n  purpose: Identificar ambiente\n",
        summary="Resumo editado pelo operador.",
        required_inputs=["container_name"],
        safety_rules=["Não reiniciar servidor"],
        validation_notes=["Confirmar retorno da coleta"],
        import_notes=["Texto revisado manualmente"],
        source_filename="procedimento.pdf",
        settings=settings,
    )

    payload = yaml.safe_load((Path(settings.agent_playbook_dir) / "checkmk-importado.yml").read_text())
    assert payload["summary"] == "Resumo editado pelo operador."
    assert payload["required_inputs"] == ["container_name"]
    assert payload["safety_rules"] == ["Não reiniciar servidor"]
    assert payload["validation"] == []
    assert payload["validation_notes"] == ["Confirmar retorno da coleta"]
    assert payload["import_notes"] == ["Texto revisado manualmente"]
    assert payload["source"]["filename"] == "procedimento.pdf"


def test_review_editor_exposes_editable_fields_and_import_log() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "app" / "ui" / "ux-improvements.js").read_text(encoding="utf-8")
    for field in (
        "playbook-editor-summary",
        "playbook-editor-inputs",
        "playbook-editor-safety",
        "playbook-editor-validations",
        "playbook-editor-notes",
        "playbook-import-log",
    ):
        assert field in script
