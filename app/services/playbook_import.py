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


def preview_imported_playbook(content: str, *, filename: str = "playbook.yml") -> dict[str, Any]:
    """Converte um YAML existente em rascunho seguro para revisão na interface."""
    raw = str(content or "")
    if not raw.strip():
        raise ValueError("o arquivo de playbook está vazio")
    if len(raw.encode("utf-8")) > 100_000:
        raise ValueError("o arquivo de playbook deve ter no máximo 100 KB")

    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML de playbook inválido: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("o YAML importado precisa conter um objeto de playbook")

    playbook_id = str(payload.get("id") or Path(filename).stem).strip().lower()
    if not _PLAYBOOK_ID.fullmatch(playbook_id):
        raise ValueError("identificador importado deve usar letras minúsculas, números, hífen ou sublinhado")

    title = str(payload.get("title") or playbook_id).strip()
    if not 3 <= len(title) <= 160:
        raise ValueError("título importado deve ter entre 3 e 160 caracteres")

    try:
        priority = int(payload.get("priority") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("prioridade importada é inválida") from exc
    if not 0 <= priority <= 999:
        raise ValueError("prioridade importada deve estar entre 0 e 999")

    if payload.get("allowed_corrections"):
        raise ValueError("o importador web aceita somente playbooks de leitura, sem correções permitidas")
    if payload.get("validation"):
        raise ValueError("o importador web ainda não aceita pós-validações; remova a seção validation e revise o arquivo")

    match = payload.get("match") or {}
    if not isinstance(match, dict):
        raise ValueError("a seção match do playbook precisa ser um objeto")
    patterns = _safe_patterns(list(match.get("any") or payload.get("patterns") or []))
    profiles = _safe_profiles(list(payload.get("profiles") or ["any"]))

    raw_steps = payload.get("steps") or []
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
    }
