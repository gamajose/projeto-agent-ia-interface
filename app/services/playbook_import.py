from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.services.playbook_editor import (
    _PLAYBOOK_ID,
    _parse_steps,
    _safe_patterns,
    _safe_profiles,
)


def _as_list(value: Any, *, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        rendered = value.strip()
        return [rendered] if rendered else []
    raise ValueError(f"o campo {field} precisa ser uma lista ou texto")


def _load_single_document(raw: str) -> dict[str, Any]:
    try:
        documents = [item for item in yaml.safe_load_all(raw) if item is not None]
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML de playbook inválido: {exc}") from exc
    if not documents:
        raise ValueError("o arquivo de playbook está vazio")
    if len(documents) > 1:
        raise ValueError("importe um playbook por arquivo YAML")
    payload = documents[0]
    if isinstance(payload, dict) and isinstance(payload.get("playbook"), dict):
        payload = payload["playbook"]
    if not isinstance(payload, dict):
        raise ValueError("o YAML importado precisa conter um objeto de playbook")
    return payload


def preview_imported_playbook(content: str, *, filename: str = "playbook.yml") -> dict[str, Any]:
    """Converte um YAML existente em rascunho seguro para revisão na interface."""
    raw = str(content or "")
    if not raw.strip():
        raise ValueError("o arquivo de playbook está vazio")
    if len(raw.encode("utf-8")) > 100_000:
        raise ValueError("o arquivo de playbook deve ter no máximo 100 KB")

    payload = _load_single_document(raw)
    warnings: list[str] = []

    playbook_id = str(payload.get("id") or payload.get("name") or Path(filename).stem).strip().lower()
    if not _PLAYBOOK_ID.fullmatch(playbook_id):
        raise ValueError("identificador importado deve usar letras minúsculas, números, hífen ou sublinhado")

    title = str(payload.get("title") or payload.get("name") or playbook_id).strip()
    if not 3 <= len(title) <= 160:
        raise ValueError("título importado deve ter entre 3 e 160 caracteres")

    try:
        priority = int(payload.get("priority") if payload.get("priority") is not None else 20)
    except (TypeError, ValueError) as exc:
        raise ValueError("prioridade importada é inválida") from exc
    if not 0 <= priority <= 999:
        raise ValueError("prioridade importada deve estar entre 0 e 999")

    if payload.get("allowed_corrections"):
        warnings.append(
            "A seção allowed_corrections foi removida do rascunho. A importação pela interface aceita somente etapas de leitura."
        )
    if payload.get("validation"):
        warnings.append(
            "A seção validation foi removida do rascunho. Pós-validações devem ser revisadas e adicionadas fora do importador web."
        )

    match = payload.get("match") or {}
    if isinstance(match, str):
        match = {"any": [match]}
    if not isinstance(match, dict):
        raise ValueError("a seção match do playbook precisa ser um objeto")

    pattern_source = match.get("any")
    if pattern_source is None:
        pattern_source = match.get("all")
    if pattern_source is None:
        pattern_source = payload.get("patterns")
    patterns = _safe_patterns(_as_list(pattern_source, field="match.any"))
    profiles = _safe_profiles(_as_list(payload.get("profiles") or ["any"], field="profiles"))

    raw_steps = payload.get("steps")
    if raw_steps is None:
        raw_steps = payload.get("checks")
    raw_steps = _as_list(raw_steps, field="steps")
    steps_yaml = yaml.safe_dump(raw_steps, allow_unicode=True, sort_keys=False, width=110)
    validated_steps = _parse_steps(steps_yaml)

    return {
        "id": playbook_id,
        "title": title,
        "priority": priority,
        "profiles": profiles,
        "patterns": patterns,
        "steps_yaml": yaml.safe_dump(validated_steps, allow_unicode=True, sort_keys=False, width=110),
        "source_filename": Path(filename).name[:255],
        "import_warnings": warnings,
    }
