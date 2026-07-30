from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.ai_providers import ProviderError
from app.services.intelligent_playbook_import import preview_intelligent_import
from app.services.persistence import get_investigation
from app.services.playbook_editor import draft_playbook, save_playbook
from app.services.playbook_import import preview_imported_playbook
from app.web import _require_access, _require_mutation

router = APIRouter(tags=["interface-playbooks"])


class PlaybookCreatePayload(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=3, max_length=160)
    priority: int = Field(default=20, ge=0, le=999)
    profiles: list[str] = Field(default_factory=lambda: ["any"], max_length=20)
    patterns: list[str] = Field(min_length=1, max_length=30)
    steps_yaml: str = Field(min_length=3, max_length=30000)
    summary: str = Field(default="", max_length=4000)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    safety_rules: list[str] = Field(default_factory=list, max_length=30)
    validation_notes: list[str] = Field(default_factory=list, max_length=30)
    import_notes: list[str] = Field(default_factory=list, max_length=30)
    source_filename: str = Field(default="", max_length=255)


class PlaybookImportPayload(BaseModel):
    filename: str = Field(default="playbook.yml", min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100000)


class IntelligentPlaybookImportPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=7_500_000)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=255)


@router.post("/ui/api/playbooks")
def create_playbook(payload: PlaybookCreatePayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    try:
        item = save_playbook(
            playbook_id=payload.id,
            title=payload.title,
            priority=payload.priority,
            profiles=payload.profiles,
            patterns=payload.patterns,
            steps_yaml=payload.steps_yaml,
            summary=payload.summary,
            required_inputs=payload.required_inputs,
            safety_rules=payload.safety_rules,
            validation_notes=payload.validation_notes,
            import_notes=payload.import_notes,
            source_filename=payload.source_filename,
            settings=get_settings(),
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"não foi possível gravar o playbook: {exc}") from exc
    return {"saved": True, "message": "Playbook salvo e carregado no catálogo.", "item": item}


@router.post("/ui/api/playbooks/import-preview")
def import_playbook_preview(payload: PlaybookImportPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    try:
        draft = preview_imported_playbook(payload.content, filename=payload.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise HTTPException(status_code=500, detail=f"não foi possível processar o YAML importado: {detail[:500]}") from exc
    return {"draft": draft, "message": "Playbook importado para revisão. Nenhum arquivo foi salvo ainda."}


@router.post("/ui/api/playbooks/intelligent-import-preview")
def intelligent_import_preview(payload: IntelligentPlaybookImportPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    try:
        draft = preview_intelligent_import(
            filename=payload.filename,
            content_base64=payload.content_base64,
            provider=payload.provider,
            model=payload.model,
            settings=get_settings(),
        )
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=f"IA indisponível para importação: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise HTTPException(status_code=500, detail=f"não foi possível analisar o documento: {detail[:500]}") from exc
    mode = draft.get("import_mode")
    message = "YAML compatível importado para revisão." if mode == "structured" else "Documento analisado pela IA e convertido em rascunho seguro para revisão."
    return {"draft": draft, "message": message}


@router.post("/ui/api/investigations/{investigation_id}/playbook-draft")
def investigation_playbook_draft(investigation_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    ensure_database_schema()
    investigation = get_investigation(investigation_id, include_evidence=True)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigação não encontrada")
    try:
        return {"draft": draft_playbook(investigation), "message": "Rascunho criado a partir das ferramentas e do objetivo da investigação."}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/ui/api/playbooks/storage")
def playbook_storage(request: Request) -> dict[str, Any]:
    _require_access(request)
    settings = get_settings()
    return {"backend": "yaml_files", "directory": settings.agent_playbook_dir, "database_role": "histórico de uso, resultado e efetividade"}
