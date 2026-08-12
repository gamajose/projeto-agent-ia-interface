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
    forbidden = ("senha", "password", "secret", "community", "token")
    assert all(not any(word in field for word in forbidden) for field in fields)


def test_n2_ui_connects_template_to_existing_investigation_flow() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    assert "Documentação e validação com IA" in source
    assert "Montar rascunho N2" in source
    assert "Investigar com IA" in source
    assert "/ui/api/n2/sites" in source
    assert "/ui/api/n2/draft" in source
    assert "nunca reinicie o servidor" in source


def test_n2_is_optional_tool_inside_projects_and_does_not_replace_navigation() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    assert "#view-projects .project-builder-head" in source
    assert "Área N2" in source
    assert "n2-workspace-modal" in source
    assert 'data-view="n2"' not in source
    assert "view-n2" not in source
    assert "showView(\"n2\")" not in source


def test_n2_backend_marks_unknown_fields_as_pending_instead_of_inventing() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "n2_workspace.py").read_text(encoding="utf-8")
    assert '"missing"' in source
    assert "Não inferir versão de banco" in source
    assert "credentials_included" in source
    assert '"credentials_included": False' in source
    assert "Nunca executar reboot, shutdown, poweroff ou halt" in source
    assert "template_teste" in source
