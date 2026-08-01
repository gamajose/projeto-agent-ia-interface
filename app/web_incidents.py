from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.base import ensure_database_schema
from app.services.persistence import (
    get_investigation,
    get_playbook_draft,
    list_investigation_feedback,
    save_investigation_feedback,
)
from app.services.playbook_drafts import (
    activate_playbook_draft,
    generate_playbook_draft,
    reject_playbook_draft,
)
from app.web import _operator_name, _require_access, _require_mutation


router = APIRouter(tags=["interface-incidents"])


class FeedbackPayload(BaseModel):
    verdict: Literal["confirmed", "partial", "rejected"]
    comment: str | None = Field(default=None, max_length=4000)
    confirmed_cause: str | None = Field(default=None, max_length=4000)


class DraftReviewPayload(BaseModel):
    action: Literal["approve", "reject"]
    notes: str | None = Field(default=None, max_length=4000)


@router.get("/ui/api/investigations/{investigation_id}/feedback")
def investigation_feedback(investigation_id: str, request: Request) -> dict:
    _require_access(request)
    ensure_database_schema()
    try:
        items = list_investigation_feedback(investigation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="investigação inválida") from exc
    counts = {"confirmed": 0, "partial": 0, "rejected": 0}
    for item in items:
        verdict = str(item.get("verdict") or "")
        if verdict in counts:
            counts[verdict] += 1
    return {"total": len(items), "counts": counts, "items": items}


@router.post("/ui/api/investigations/{investigation_id}/feedback")
def submit_investigation_feedback(
    investigation_id: str,
    payload: FeedbackPayload,
    request: Request,
) -> dict:
    _require_mutation(request)
    ensure_database_schema()
    try:
        item = save_investigation_feedback(
            investigation_id,
            operator=_operator_name(),
            verdict=payload.verdict,
            comment=payload.comment,
            confirmed_cause=payload.confirmed_cause,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "feedback": item}


@router.post("/ui/api/investigations/{investigation_id}/playbook-draft")
def create_investigation_playbook_draft(investigation_id: str, request: Request) -> dict:
    _require_mutation(request)
    ensure_database_schema()
    investigation = get_investigation(investigation_id, include_evidence=True)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigação não encontrada")
    analysis = dict(investigation.get("analysis") or {})
    comparison = dict(analysis.get("correction_validation") or {})
    if comparison.get("status") != "validated":
        raise HTTPException(
            status_code=409,
            detail="o playbook só pode ser gerado após uma correção com pós-validação completa",
        )
    try:
        draft = generate_playbook_draft(
            investigation_id,
            list(comparison.get("actions") or []),
            generated_by=_operator_name(),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"created": bool(draft), "draft": draft}


@router.get("/ui/api/playbook-drafts/{draft_id}")
def playbook_draft_detail(draft_id: str, request: Request) -> dict:
    _require_access(request)
    ensure_database_schema()
    draft = get_playbook_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="rascunho de playbook não encontrado")
    return draft


@router.post("/ui/api/playbook-drafts/{draft_id}/review")
def review_draft(draft_id: str, payload: DraftReviewPayload, request: Request) -> dict:
    _require_mutation(request)
    ensure_database_schema()
    try:
        if payload.action == "approve":
            draft = activate_playbook_draft(
                draft_id,
                reviewed_by=_operator_name(),
                review_notes=payload.notes,
            )
        else:
            draft = reject_playbook_draft(
                draft_id,
                reviewed_by=_operator_name(),
                review_notes=payload.notes,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"reviewed": True, "draft": draft}
