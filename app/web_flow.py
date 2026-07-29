from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.settings import get_settings
from app.services.correction_continuation import (
    CorrectionContinuationError,
    prepare_correction_continuation,
)
from app.services.inventory_learning import (
    backfill_inventory_from_history,
    target_suggestions,
)
from app.services.persistence import get_investigation
from app.services.result_presentation import finalize_result_presentation
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-flow"])


@router.get("/ui/api/targets/suggestions")
def target_suggestion_list(
    request: Request,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=12, ge=1, le=50),
) -> dict[str, Any]:
    _require_access(request)
    items = target_suggestions(q, limit=limit, settings=get_settings())
    return {"total": len(items), "items": items}


@router.post("/ui/api/inventory/backfill")
def inventory_backfill(request: Request) -> dict[str, Any]:
    _require_mutation(request)
    result = backfill_inventory_from_history(settings=get_settings(), force=True)
    return {
        **result,
        "message": "Inventário reconciliado com o histórico de investigações.",
    }


@router.post("/ui/api/investigations/{investigation_id}/prepare-correction")
def prepare_correction(investigation_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    try:
        return prepare_correction_continuation(
            investigation_id,
            settings=get_settings(),
        )
    except CorrectionContinuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/ui/api/investigations/{investigation_id}/normalize-presentation")
def normalize_presentation(investigation_id: str, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    result = get_investigation(investigation_id, include_evidence=True)
    if not result:
        raise HTTPException(status_code=404, detail="investigação não encontrada")
    result["investigation_id"] = investigation_id
    result["selected_model"] = result.get("model")
    finalize_result_presentation(result, settings=get_settings())
    return result
