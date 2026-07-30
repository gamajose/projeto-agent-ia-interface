from __future__ import annotations

import base64
import io
import zipfile

import yaml

from app.services.intelligent_playbook_import import extract_document, preview_intelligent_import


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _docx(text: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _mock_tools(monkeypatch) -> None:
    tools = lambda: [{"name": "system.basics", "description": "Básico", "correction": False}]
    monkeypatch.setattr("app.services.intelligent_playbook_import.describe_tools", tools)
    monkeypatch.setattr("app.services.playbook_editor.describe_tools", tools)


def test_extract_document_reads_docx_text() -> None:
    text, suffix = extract_document("procedimento.docx", _encoded(_docx("Recuperar sensores Checkmk")))
    assert suffix == ".docx"
    assert "Recuperar sensores Checkmk" in text


def test_yaml_compatible_uses_structured_import(monkeypatch) -> None:
    _mock_tools(monkeypatch)
    raw = yaml.safe_dump(
        {
            "id": "checkmk-basico",
            "title": "Diagnóstico Checkmk",
            "priority": 20,
            "profiles": ["checkmk"],
            "match": {"any": ["Check_MK"]},
            "steps": [{"tool": "system.basics", "arguments": {}, "purpose": "Identificar host"}],
        },
        sort_keys=False,
    )
    draft = preview_intelligent_import(filename="playbook.yml", content_base64=_encoded(raw.encode()))
    assert draft["import_mode"] == "structured"
    assert draft["id"] == "checkmk-basico"


def test_text_document_is_converted_by_provider(monkeypatch) -> None:
    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def generate_json(self, prompt: str):
            assert "Missing monitoring data" in prompt
            return (
                {
                    "id": "checkmk-sensores-container",
                    "title": "Recuperação de sensores Checkmk em container",
                    "priority": 95,
                    "profiles": ["checkmk"],
                    "patterns": ["Missing monitoring data", "Item not found"],
                    "steps": [
                        {"tool": "system.basics", "arguments": {}, "purpose": "Identificar o ambiente"}
                    ],
                    "import_warnings": ["Instalação do agente removida do rascunho"],
                    "safety_rules": ["Não reiniciar servidor ou container"],
                    "validation_notes": ["Confirmar retorno das seções internas"],
                },
                {"response_chars": 100},
            )

    _mock_tools(monkeypatch)
    monkeypatch.setattr(
        "app.services.intelligent_playbook_import.get_provider",
        lambda *args, **kwargs: FakeProvider(),
    )

    draft = preview_intelligent_import(
        filename="procedimento.txt",
        content_base64=_encoded(b"Missing monitoring data for plugins"),
    )
    assert draft["import_mode"] == "intelligent"
    assert draft["id"] == "checkmk-sensores-container"
    assert draft["ai_metadata"]["provider"] == "fake"
    assert yaml.safe_load(draft["steps_yaml"])[0]["tool"] == "system.basics"
