from __future__ import annotations

from pathlib import Path

from app.services.n2_workspace import N2_TEMPLATE_SECTIONS
from app.web_ui_cache import _inject_n2_shell


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


def test_n2_is_injected_as_a_real_main_navigation_shell() -> None:
    html = '<nav class="nav"><button class="nav-item" data-view="projects"><span>Projetos</span></button></nav><main class="main"><section class="view" id="view-opencode"></section></main>'
    rendered = _inject_n2_shell(html)
    assert 'data-view="n2"' in rendered
    assert '<span>N2</span>' in rendered
    assert 'id="view-n2"' in rendered
    assert rendered.index('data-view="projects"') < rendered.index('data-view="n2"')


def test_n2_runtime_uses_dedicated_module_not_project_workspace() -> None:
    cache = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    source = (PROJECT_ROOT / "app" / "ui" / "n2-documentation.js").read_text(encoding="utf-8")
    assert 'n2-documentation.js' in cache
    assert 'n2-workspace.js", marker="n2-workspace"' not in cache
    assert 'id="n2-client-search" role="combobox"' in source
    assert 'id="n2-host-list"' in source
    assert 'id="n2-playbook"' in source
    assert "#view-projects .project-builder-head" not in source


def test_n2_client_selector_is_searchable_and_legible() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-documentation.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.css").read_text(encoding="utf-8")
    assert 'id="n2-client-search" role="combobox"' in source
    assert 'id="n2-client-options" role="listbox"' in source
    assert "renderClientOptions" in source
    assert 'select id="n2-site"' not in source
    assert "background:#f8fafc" in css
    assert ".n2-client-option:hover" in css


def test_n2_requires_explicit_hosts_and_supports_optional_playbook() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-documentation.js").read_text(encoding="utf-8")
    assert "data-n2-host-select" in source
    assert "n2-select-all-hosts" in source
    assert "n2-clear-hosts" in source
    assert 'id="n2-playbook"' in source
    assert "Automático — a IA decide o roteiro de validação" in source
    assert '/ui/api/playbooks' in source
    assert "Selecione pelo menos um host para a documentação" in source


def test_n2_runs_selected_hosts_then_builds_editable_review() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-documentation.js").read_text(encoding="utf-8")
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


def test_n2_persists_documents_and_can_reopen_them() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-documentation.js").read_text(encoding="utf-8")
    web = (PROJECT_ROOT / "app" / "web_n2.py").read_text(encoding="utf-8")
    model = (PROJECT_ROOT / "app" / "db" / "n2_models.py").read_text(encoding="utf-8")
    store = (PROJECT_ROOT / "app" / "services" / "n2_document_store.py").read_text(encoding="utf-8")
    assert "DOCUMENTOS SALVOS" in source
    assert 'id="n2-document-list"' in source
    assert "/ui/api/n2/documents" in source
    assert "saveReview" in source
    assert "openDocument" in source
    assert '@router.get("/ui/api/n2/documents")' in web
    assert '@router.post("/ui/api/n2/documents")' in web
    assert 'review["document_id"] = saved["id"]' in web
    assert '__tablename__ = "n2_documents"' in model
    assert "review_payload" in model
    assert "sanitize_n2_review" in store


def test_customers_empty_navigation_is_removed_in_favor_of_n2_client_search() -> None:
    policy = (PROJECT_ROOT / "app" / "ui" / "navigation-policy.js").read_text(encoding="utf-8")
    assert '.nav-item[data-view="customers"]' in policy
    assert "item.remove()" in policy
    assert '#view-customers' in policy
    assert '.nav-item[data-view="n2"]' in policy


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
