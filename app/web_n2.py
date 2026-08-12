from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.n2_workspace import build_n2_documentation_draft, list_n2_sites, n2_site_context
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["n2-workspace"])


class N2DraftPayload(BaseModel):
    site_id: str = Field(min_length=1, max_length=64)
    responsibles: dict[str, str] = Field(default_factory=dict)


@router.get("/ui/api/n2/sites")
def n2_sites(
    request: Request,
    query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict:
    _require_access(request)
    return list_n2_sites(query=query, limit=limit)


@router.get("/ui/api/n2/sites/{site_id}")
def n2_site(site_id: str, request: Request) -> dict:
    _require_access(request)
    result = n2_site_context(site_id)
    if result is None:
        raise HTTPException(status_code=404, detail="cliente/site não encontrado")
    return result


@router.post("/ui/api/n2/draft")
def n2_draft(payload: N2DraftPayload, request: Request) -> dict:
    _require_mutation(request)
    try:
        return build_n2_documentation_draft(payload.site_id, responsibles=payload.responsibles)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"workspace N2 indisponível: {type(exc).__name__}: {exc}",
        ) from exc
