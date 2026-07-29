from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.persistence import get_investigation
from app.services.playbook_editor import draft_playbook, save_playbook
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-playbooks"])


class PlaybookCreatePayload(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=3, max_length=160)
    priority: int = Field(default=20, ge=0, le=999)
    profiles: list[str] = Field(default_factory=lambda: ["any"], max_length=20)
    patterns: list[str] = Field(min_length=1, max_length=30)
    steps_yaml: str = Field(min_length=3, max_length=30000)


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
            settings=get_settings(),
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"não foi possível gravar o playbook: {exc}") from exc
    return {
        "saved": True,
        "message": "Playbook salvo e carregado no catálogo.",
        "item": item,
    }


@router.post("/ui/api/investigations/{investigation_id}/playbook-draft")
def investigation_playbook_draft(investigation_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    ensure_database_schema()
    investigation = get_investigation(investigation_id, include_evidence=True)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigação não encontrada")
    try:
        return {
            "draft": draft_playbook(investigation),
            "message": "Rascunho criado a partir das ferramentas e do objetivo da investigação.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/ui/api/playbooks/storage")
def playbook_storage(request: Request) -> dict[str, Any]:
    _require_access(request)
    settings = get_settings()
    return {
        "backend": "yaml_files",
        "directory": settings.agent_playbook_dir,
        "database_role": "histórico de uso, resultado e efetividade",
    }
