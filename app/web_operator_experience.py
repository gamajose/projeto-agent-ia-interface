from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.db.base import ensure_database_schema
from app.services.checkmk_customer_sync import sync_checkmk_customers_from_inventory
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
    # O CMK05 já conhece cliente/site/hosts. Materializamos esses dados na
    # topologia persistida para que a aba Clientes deixe de depender de uma
    # investigação multi-host anterior.
    sync_checkmk_customers_from_inventory()
    return list_customer_overviews(query=query, limit=limit)


@router.get("/ui/api/customers/{customer_id}")
def customer_overview(customer_id: str, request: Request) -> dict:
    _require_access(request)
    ensure_database_schema()
    sync_checkmk_customers_from_inventory()
    result = get_customer_overview(customer_id)
    if not result:
        raise HTTPException(status_code=404, detail="cliente não encontrado")
    return result
