from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import Settings, get_settings
from app.services.playbook_editor import (
    _PLAYBOOK_ID,
    _parse_steps,
    _safe_patterns,
    _safe_profiles,
    _safe_text_list,
)
from app.services.playbooks import reload_playbooks


def _playbook_path(playbook_id: str, settings: Settings) -> Path:
    normalized = str(playbook_id or "").strip().lower()
    if not _PLAYBOOK_ID.fullmatch(normalized):
        raise ValueError("identificador de playbook inválido")
    directory = Path(settings.agent_playbook_dir).expanduser()
    return directory / f"{normalized}.yml"


def _load_raw(playbook_id: str, settings: Settings) -> tuple[Path, dict[str, Any]]:
    path = _playbook_path(playbook_id, settings)
    if not path.is_file():
        raise LookupError(f"playbook '{playbook_id}' não encontrado")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"playbook '{playbook_id}' possui YAML inválido")
    return path, payload


def _source_filename(payload: dict[str, Any]) -> str:
    source = payload.get("source") or {}
    if isinstance(source, dict):
        return str(source.get("filename") or "")
    return ""


def read_playbook_document(playbook_id: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    path, payload = _load_raw(playbook_id, settings)
    match = payload.get("match") or {}
    patterns = list(match.get("any") or ()) if isinstance(match, dict) else []
    steps = list(payload.get("steps") or ())
    return {
        "id": str(payload.get("id") or path.stem),
        "title": str(payload.get("title") or path.stem),
        "priority": int(payload.get("priority") or 0),
        "profiles": [str(item) for item in payload.get("profiles") or ("any",)],
        "patterns": [str(item) for item in patterns],
        "summary": str(payload.get("summary") or ""),
        "required_inputs": [str(item) for item in payload.get("required_inputs") or ()],
        "safety_rules": [str(item) for item in payload.get("safety_rules") or ()],
        "validation_notes": [str(item) for item in payload.get("validation_notes") or ()],
        "import_notes": [str(item) for item in payload.get("import_notes") or ()],
        "source_filename": _source_filename(payload),
        "steps_yaml": yaml.safe_dump(steps, allow_unicode=True, sort_keys=False, width=110),
        "allowed_corrections": [str(item) for item in payload.get("allowed_corrections") or ()],
        "validation": list(payload.get("validation") or ()),
        "ssh_port": payload.get("ssh_port"),
        "file": path.name,
    }


def list_playbook_documents(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    directory = Path(settings.agent_playbook_dir).expanduser()
    if not directory.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yml")):
        try:
            result.append(read_playbook_document(path.stem, settings))
        except (LookupError, ValueError, OSError, yaml.YAMLError):
            continue
    return sorted(result, key=lambda item: (-int(item.get("priority") or 0), str(item.get("title") or item.get("id") or "")))


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)
    temporary = path.parent / f".{path.stem}.{os.getpid()}.tmp"
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def update_playbook_document(
    playbook_id: str,
    *,
    title: str,
    priority: int,
    profiles: list[str],
    patterns: list[str],
    steps_yaml: str,
    summary: str = "",
    required_inputs: list[str] | None = None,
    safety_rules: list[str] | None = None,
    validation_notes: list[str] | None = None,
    import_notes: list[str] | None = None,
    source_filename: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    path, existing = _load_raw(playbook_id, settings)
    normalized_id = str(playbook_id or "").strip().lower()
    normalized_title = str(title or "").strip()
    if not 3 <= len(normalized_title) <= 160:
        raise ValueError("título deve ter entre 3 e 160 caracteres")
    normalized_priority = int(priority)
    if not 0 <= normalized_priority <= 999:
        raise ValueError("prioridade deve estar entre 0 e 999")

    existing_steps = list(existing.get("steps") or ())
    try:
        submitted_steps = yaml.safe_load(steps_yaml or "[]")
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML de etapas inválido: {exc}") from exc
    if submitted_steps == existing_steps:
        validated_steps = existing_steps
    else:
        validated_steps = _parse_steps(steps_yaml)

    payload = dict(existing)
    payload.update(
        {
            "id": normalized_id,
            "title": normalized_title,
            "priority": normalized_priority,
            "profiles": _safe_profiles(profiles),
            "match": {**(existing.get("match") or {}), "any": _safe_patterns(patterns)},
            "summary": str(summary or "").strip()[:4000],
            "required_inputs": _safe_text_list(required_inputs, item_limit=160),
            "safety_rules": _safe_text_list(safety_rules, item_limit=400),
            "steps": validated_steps,
            "validation_notes": _safe_text_list(validation_notes, item_limit=400),
            "import_notes": _safe_text_list(import_notes, item_limit=500),
        }
    )
    if source_filename:
        payload["source"] = {"filename": Path(source_filename).name[:255]}

    _write_atomic(path, payload)
    reload_playbooks()
    return read_playbook_document(normalized_id, settings)


def delete_playbook_document(playbook_id: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    path, payload = _load_raw(playbook_id, settings)
    title = str(payload.get("title") or playbook_id)
    path.unlink()
    reload_playbooks()
    return {"id": str(playbook_id).strip().lower(), "title": title, "removed": True}
