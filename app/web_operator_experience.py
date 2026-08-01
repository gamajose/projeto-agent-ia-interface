from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.db.base import ensure_database_schema
from app.services.customer_overview import get_customer_overview, list_customer_overviews
from app.web import _require_access


router = APIRouter(tags=["operator-experience"])


@router.get("/ui/api/customers")
def customer_overviews(
    request: Request,
    query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict:
    _require_access(request)
    ensure_database_schema()
    return list_customer_overviews(query=query, limit=limit)


@router.get("/ui/api/customers/{customer_id}")
def customer_overview(customer_id: str, request: Request) -> dict:
    _require_access(request)
    ensure_database_schema()
    result = get_customer_overview(customer_id)
    if not result:
        raise HTTPException(status_code=404, detail="cliente não encontrado")
    return result
