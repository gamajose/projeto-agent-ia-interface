from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import yaml

from app.core.settings import Settings, get_settings
from app.services.ai_providers import ProviderError, get_provider
from app.services.playbook_editor import _parse_steps, _safe_patterns, _safe_profiles, _slug
from app.services.playbook_import import preview_imported_playbook
from app.services.tool_registry import describe_tools

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_ALLOWED_EXTENSIONS = {".yaml", ".yml", ".txt", ".md", ".docx", ".pdf"}
_AUTO_SELECTIONS = {"", "auto", "automatic", "automatico", "automático", "default", "padrao", "padrão"}


def _decode_payload(content_base64: str) -> bytes:
    try:
        data = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError("conteúdo do arquivo inválido") from exc
    if not data:
        raise ValueError("o arquivo enviado está vazio")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("o documento deve ter no máximo 5 MB")
    return data


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("arquivo DOCX inválido ou corrompido") from exc
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    blocks: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if text:
            blocks.append(text)
    if not blocks:
        raise ValueError("não foi possível encontrar texto no DOCX")
    return "\n".join(blocks)


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("suporte a PDF indisponível; instale a dependência pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise ValueError("não foi possível ler o PDF") from exc
    text = "\n\n".join(page for page in pages if page)
    if len(text.strip()) < 40:
        raise ValueError("o PDF não possui texto extraível; envie um PDF textual ou DOCX")
    return text


def extract_document(filename: str, content_base64: str) -> tuple[str, str]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise ValueError("formato não suportado; use YAML, YML, TXT, MD, DOCX ou PDF")
    data = _decode_payload(content_base64)
    if suffix == ".docx":
        text = _extract_docx(data)
    elif suffix == ".pdf":
        text = _extract_pdf(data)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("o arquivo de texto precisa estar em UTF-8") from exc
    text = text.replace("\x00", "").strip()
    if not text:
        raise ValueError("o documento não contém texto útil")
    return text[:120_000], suffix


def _tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": item.get("name"),
            "description": item.get("description"),
            "correction": bool(item.get("correction")),
            "arguments": item.get("arguments") or item.get("schema") or {},
        }
        for item in describe_tools()
        if item.get("name")
    ]


def _prompt(document: str, filename: str) -> str:
    tools = _tool_catalog()
    return f"""Você é um engenheiro de confiabilidade criando um playbook reutilizável para um agente de infraestrutura.
Converta o documento abaixo em UM rascunho JSON seguro. Não invente fatos ausentes.

Regras obrigatórias:
- Generalize nomes de clientes, IPs, hostnames, sites e containers em variáveis nos argumentos.
- O playbook importado pela interface é somente de investigação/leitura: não inclua reinício, instalação, remoção, alteração, download ou acesso a banco.
- Use exclusivamente ferramentas do catálogo com correction=false.
- Gere padrões regex simples a partir de sintomas, mensagens de erro e objetivo; não copie comandos como padrões.
- Preserve causa provável, resultado esperado e restrições como avisos em import_warnings.
- Produção e standby nunca podem reiniciar servidor ou container.
- Retorne apenas JSON com: id, title, priority, profiles, patterns, steps, import_warnings, extracted_summary, required_inputs, safety_rules, validation_notes.
- steps é lista de objetos com tool, arguments e purpose.
- id deve ter 2 a 64 caracteres: minúsculas, números, hífen ou sublinhado.

Catálogo de ferramentas:
{json.dumps(tools, ensure_ascii=False)[:30000]}

Arquivo: {filename}
Documento:
---
{document}
---
"""


def _as_string_list(
    value: Any,
    *,
    item_limit: int,
    limit: int = 30,
    split_lines: bool = True,
) -> list[str]:
    """Normaliza lista ou texto único sem transformar uma string em caracteres."""
    if value is None:
        raw_items: list[Any] = []
    elif isinstance(value, str):
        raw_items = value.splitlines() if split_lines else [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    result: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if not text:
            continue
        text = text[:item_limit]
        if text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _normalize_ai_result(payload: dict[str, Any], filename: str) -> dict[str, Any]:
    title = str(payload.get("title") or Path(filename).stem or "Playbook importado").strip()[:160]
    playbook_id = _slug(str(payload.get("id") or title))[:64]
    try:
        priority = max(0, min(999, int(payload.get("priority", 20))))
    except (TypeError, ValueError):
        priority = 20

    profiles = _safe_profiles(
        _as_string_list(payload.get("profiles") or ["any"], item_limit=64)
    )
    patterns = _safe_patterns(
        _as_string_list(payload.get("patterns") or [], item_limit=500)
    )
    steps_raw = payload.get("steps") or []
    steps_yaml = yaml.safe_dump(steps_raw, allow_unicode=True, sort_keys=False, width=110)
    validated_steps = _parse_steps(steps_yaml)

    return {
        "id": playbook_id,
        "title": title,
        "priority": priority,
        "profiles": profiles,
        "patterns": patterns,
        "steps_yaml": yaml.safe_dump(validated_steps, allow_unicode=True, sort_keys=False, width=110),
        "source_filename": Path(filename).name[:255],
        "import_warnings": _as_string_list(payload.get("import_warnings"), item_limit=500),
        "extracted_summary": str(payload.get("extracted_summary") or "").strip()[:2000],
        "required_inputs": _as_string_list(payload.get("required_inputs"), item_limit=120),
        "safety_rules": _as_string_list(payload.get("safety_rules"), item_limit=300),
        "validation_notes": _as_string_list(payload.get("validation_notes"), item_limit=300),
        "import_mode": "intelligent",
    }


def _automatic_as_none(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if normalized.casefold() in _AUTO_SELECTIONS:
        return None
    return normalized


def preview_intelligent_import(
    *,
    filename: str,
    content_base64: str,
    provider: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    document, suffix = extract_document(filename, content_base64)

    if suffix in {".yaml", ".yml"}:
        try:
            draft = preview_imported_playbook(document, filename=filename)
            draft["import_mode"] = "structured"
            return draft
        except ValueError:
            pass

    selected_provider = _automatic_as_none(provider)
    selected_model = _automatic_as_none(model)
    try:
        ai = get_provider(selected_provider, settings=settings, model_name=selected_model)
        result, metadata = ai.generate_json(_prompt(document, filename))
    except ProviderError:
        raise
    except Exception as exc:
        raise ValueError(f"a IA não conseguiu interpretar o documento: {str(exc)[:300]}") from exc

    draft = _normalize_ai_result(result, filename)
    draft["ai_metadata"] = {
        "provider": getattr(ai, "name", selected_provider or "automático"),
        "model": getattr(ai, "model", selected_model or ""),
        **metadata,
    }
    return draft
