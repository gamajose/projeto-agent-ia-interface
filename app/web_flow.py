from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services.correction_continuation import (
    CorrectionContinuationError,
    prepare_correction_continuation,
)
from app.services.inventory_learning import (
    backfill_inventory_from_history,
    target_suggestions,
)
from app.services.persistence import (
    get_investigation,
    resolve_saved_target,
)
from app.services.result_presentation import finalize_result_presentation
from app.services.runner import run_target
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


@router.post("/ui/api/investigations/{investigation_id}/recheck")
def recheck_investigation(investigation_id: str, request: Request) -> dict[str, Any]:
    """Executa uma nova varredura depois da correção ou de reinício manual.

    A varredura gera um novo registro, mas mantém o vínculo explícito com a
    investigação anterior para que a evolução do incidente permaneça rastreável.
    """
    _require_mutation(request)
    previous = get_investigation(investigation_id, include_evidence=True)
    if not previous:
        raise HTTPException(status_code=404, detail="investigação não encontrada")

    settings = get_settings()
    try:
        environment = EnvironmentType(
            str(previous.get("environment") or EnvironmentType.UNKNOWN.value)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="ambiente da investigação é inválido") from exc

    target = str(previous.get("target") or "").strip()
    objective = str(previous.get("objective") or "").strip()
    if not target or not objective:
        raise HTTPException(status_code=409, detail="alvo ou objetivo original não está disponível")

    saved = resolve_saved_target(target, environment.value)
    ssh_port = int((saved or {}).get("ssh_port") or settings.ssh_default_port)
    provider = "auto" if settings.agent_autopilot_enabled else str(settings.ai_provider or "gemini")
    try:
        result = run_target(
            target,
            (
                objective
                + "\n\nNOVA VARREDURA: compare o estado atual com a investigação anterior "
                + investigation_id
                + ", confirme se o sintoma foi sanado e, caso permaneça, identifique o novo bloqueio."
            ),
            environment=environment,
            mode="propose",
            approve=False,
            ssh_port=ssh_port,
            provider_name=provider,
            model_name=None,
            playbook_mode="auto",
            playbook_id=None,
            settings=settings,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    result["recheck_of"] = investigation_id
    result["requested_mode"] = "propose"
    return result


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
