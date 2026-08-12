from __future__ import annotations

from pathlib import Path

from app.services.n2_workspace import N2_TEMPLATE_SECTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_n2_template_covers_operational_documentation_sections() -> None:
    ids = {item["id"] for item in N2_TEMPLATE_SECTIONS}
    assert {
        "identification",
        "inventory",
        "infrastructure",
        "database",
        "totvs_activation",
        "sgdb_tnsnames",
        "winthor_mapping",
        "backup_policy",
        "oracle_backup",
        "erp_backup",
        "backup_execution",
        "retention",
        "redundancy",
        "monitoring",
        "closing",
    }.issubset(ids)


def test_n2_template_does_not_request_password_or_secret_fields() -> None:
    fields = [str(field).casefold() for item in N2_TEMPLATE_SECTIONS for field in item.get("fields") or []]
    forbidden = ("password", "secret", "community", "token")
    for field in fields:
        assert not any(word in field for word in forbidden), field
        if "senha" in field:
            assert "sem senha" in field or "não registrar senha" in field, field


def test_n2_has_own_main_navigation_and_is_not_inside_projects() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    assert 'button.dataset.view = "n2"' in source
    assert 'view.id = "view-n2"' in source
    assert ">N2</span>" in source
    assert "#view-projects .project-builder-head" not in source
    assert "n2-workspace-modal" not in source


def test_n2_client_selector_is_searchable_custom_combobox() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.css").read_text(encoding="utf-8")
    assert 'id="n2-client-search" role="combobox"' in source
    assert 'id="n2-client-options" role="listbox"' in source
    assert "renderClientOptions" in source
    assert 'select id="n2-site"' not in source
    assert "background:#f8fafc" in css
    assert ".n2-client-option:hover" in css


def test_n2_ui_requires_explicit_host_selection_and_supports_optional_playbook() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    assert "data-n2-host-select" in source
    assert "n2-select-all-hosts" in source
    assert "n2-clear-hosts" in source
    assert 'id="n2-playbook"' in source
    assert "Automático — a IA decide o roteiro de validação" in source
    assert '/ui/api/playbooks' in source


def test_n2_ui_runs_selected_hosts_then_builds_editable_review() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    for endpoint in (
        "/ui/api/n2/plan",
        "/ui/api/executions",
        "/ui/api/n2/review",
        "/ui/api/n2/export/${format}",
    ):
        assert endpoint in source
    assert "auto_expand_scope: false" in source
    assert "data-review-host-field" in source
    assert "data-review-field" in source
    assert "Exportar Word" in source
    assert "Exportar PDF" in source


def test_n2_collection_backend_is_strictly_read_only_and_never_collects_secrets() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "n2_documentation.py").read_text(encoding="utf-8")
    assert "VALIDAÇÃO DOCUMENTAL N2 — SOMENTE LEITURA" in source
    assert "NUNCA executar reboot" in source
    assert "NUNCA reiniciar, parar, habilitar/desabilitar ou alterar serviços" in source
    assert "NUNCA acessar banco de dados do cliente por cliente SQL/RMAN" in source
    assert "NUNCA coletar, imprimir, registrar ou inferir senha" in source
    assert '"server_reboot": "absolute_denial"' in source
    assert '"secrets": "never_collect"' in source


def test_n2_export_endpoints_and_sensitive_fields_are_protected() -> None:
    web = (PROJECT_ROOT / "app" / "web_n2.py").read_text(encoding="utf-8")
    exporter = (PROJECT_ROOT / "app" / "services" / "n2_document_export.py").read_text(encoding="utf-8")
    assert '@router.post("/ui/api/n2/export/{document_format}")' in web
    assert "sanitize_n2_review" in web
    assert "SENSITIVE_KEYS" in exporter
    assert '("WINT - SYS", "")' in exporter
    assert '["URL", "Usuário", "Senha"]' in exporter
    assert '_field(values,"monitoring_user"),""' in exporter


def test_legacy_n2_draft_still_marks_unknown_fields_as_pending() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "n2_workspace.py").read_text(encoding="utf-8")
    assert '"missing"' in source
    assert "Não inferir versão de banco" in source
    assert "credentials_included" in source
    assert '"credentials_included": False' in source
    assert "Nunca executar reboot, shutdown, poweroff ou halt" in source
    assert "template_teste" in source
