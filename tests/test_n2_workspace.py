from __future__ import annotations

from pathlib import Path

from app.services.n2_workspace import N2_TEMPLATE_SECTIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_n2_template_covers_operational_documentation_sections() -> None:
    ids = {item["id"] for item in N2_TEMPLATE_SECTIONS}
    assert {
        "identification",
        "infrastructure",
        "database",
        "backup",
        "redundancy",
        "monitoring",
        "closing",
    }.issubset(ids)


def test_n2_template_does_not_request_password_fields() -> None:
    fields = [str(field).casefold() for item in N2_TEMPLATE_SECTIONS for field in item.get("fields") or []]
    assert all("senha" not in field and "password" not in field and "secret" not in field for field in fields)


def test_n2_ui_connects_template_to_existing_investigation_flow() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "n2-workspace.js").read_text(encoding="utf-8")
    assert "Documentação e validação com IA" in source
    assert "Montar rascunho N2" in source
    assert "Investigar com IA" in source
    assert "/ui/api/n2/sites" in source
    assert "/ui/api/n2/draft" in source
    assert "nunca reinicie o servidor" in source


def test_n2_backend_marks_unknown_fields_as_pending_instead_of_inventing() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "n2_workspace.py").read_text(encoding="utf-8")
    assert '"missing"' in source
    assert "Não inferir versão de banco" in source
    assert "credentials_included" in source
    assert '"credentials_included": False' in source
