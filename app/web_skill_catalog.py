from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.noc_skills import delete_noc_skill, load_noc_skills, save_noc_skill
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-noc-skills"])


class SkillMatchPayload(BaseModel):
    service: list[str] = Field(default_factory=list, max_length=50)
    output: list[str] = Field(default_factory=list, max_length=50)
    host: list[str] = Field(default_factory=list, max_length=50)


class SkillPayload(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=2, max_length=160)
    priority: int = Field(default=0, ge=-1000, le=10000)
    match: SkillMatchPayload = Field(default_factory=SkillMatchPayload)
    target_strategy: Literal["internal_ssh", "entry_context"] = "internal_ssh"
    playbook_id: str | None = Field(default=None, max_length=120)
    objective: str = Field(default="Investigar a causa do alerta usando somente evidências verificáveis.", max_length=2000)
    knowledge: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)


@router.get("/ui/api/noc/skills/catalog")
def noc_skills_catalog(request: Request) -> dict:
    _require_access(request)
    try:
        items = [skill.as_dict() for skill in load_noc_skills()]
        return {"total": len(items), "items": items}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"catálogo de skills indisponível: {type(exc).__name__}: {exc}") from exc


@router.post("/ui/api/noc/skills/catalog")
def noc_skill_save(payload: SkillPayload, request: Request) -> dict:
    _require_mutation(request)
    try:
        return save_noc_skill(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível salvar a skill: {type(exc).__name__}: {exc}") from exc


@router.delete("/ui/api/noc/skills/catalog/{skill_id}")
def noc_skill_delete(skill_id: str, request: Request) -> dict:
    _require_mutation(request)
    try:
        return delete_noc_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível remover a skill: {type(exc).__name__}: {exc}") from exc
