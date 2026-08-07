from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.db.base import ensure_database_schema
from app.services.noc_incidents import (
    acknowledge_incident,
    get_noc_incident,
    list_noc_incidents,
    noc_dashboard,
    resolve_incident,
)
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


class IncidentResolvePayload(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)


@router.get("/ui/api/noc/dashboard")
def noc_operational_dashboard(request: Request) -> dict:
    _require_access(request)
    try:
        return noc_dashboard()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"supervisor NOC indisponível: {type(exc).__name__}: {exc}") from exc


@router.get("/ui/api/noc/incidents")
def noc_incidents(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None, max_length=40),
    open_only: bool = Query(default=False),
) -> dict:
    _require_access(request)
    try:
        return list_noc_incidents(limit=limit, status=status, open_only=open_only, sync_jobs=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"supervisor NOC indisponível: {type(exc).__name__}: {exc}") from exc


@router.get("/ui/api/noc/incidents/{incident_id}")
def noc_incident_detail(incident_id: str, request: Request) -> dict:
    _require_access(request)
    try:
        incident = get_noc_incident(incident_id, include_events=True, sync_job=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"supervisor NOC indisponível: {type(exc).__name__}: {exc}") from exc
    if not incident:
        raise HTTPException(status_code=404, detail="incidente NOC não encontrado")
    return incident


@router.post("/ui/api/noc/incidents/{incident_id}/acknowledge")
def noc_incident_acknowledge(incident_id: str, request: Request) -> dict:
    _require_mutation(request)
    try:
        incident = acknowledge_incident(incident_id, operator=_operator_name())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"supervisor NOC indisponível: {type(exc).__name__}: {exc}") from exc
    if not incident:
        raise HTTPException(status_code=404, detail="incidente NOC não encontrado")
    return {"acknowledged": True, "incident": incident}


@router.post("/ui/api/noc/incidents/{incident_id}/resolve")
def noc_incident_resolve(incident_id: str, payload: IncidentResolvePayload, request: Request) -> dict:
    _require_mutation(request)
    try:
        incident = resolve_incident(
            incident_id,
            operator=_operator_name(),
            reason=payload.reason,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"supervisor NOC indisponível: {type(exc).__name__}: {exc}") from exc
    if not incident:
        raise HTTPException(status_code=404, detail="incidente NOC não encontrado")
    return {"resolved": True, "incident": incident}


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
