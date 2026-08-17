from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.noc_problem_batch import (
    current_problem_groups,
    problem_group_detail,
    request_procedure_batch,
)
from app.web import _operator_name, _require_access, _require_mutation


router = APIRouter(tags=["interface-noc-batch"])


class ProcedureBatchPayload(BaseModel):
    sites: list[str] = Field(default_factory=list, max_length=1000)


@router.get("/ui/api/noc/problem-groups")
def noc_problem_groups(request: Request) -> dict:
    """Agrupa todos os alertas atuais pelo procedure da NOC Master Skill."""

    _require_access(request)
    try:
        return current_problem_groups()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"não foi possível agrupar os problemas atuais: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/ui/api/noc/problem-groups/{procedure_id}/detail")
def noc_problem_group_detail(procedure_id: str, request: Request) -> dict:
    """Lista empresa/site, host e alertas atuais do procedure escolhido."""

    _require_access(request)
    try:
        return problem_group_detail(procedure_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"não foi possível abrir os hosts do problema: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/problem-groups/{procedure_id}/run")
def noc_problem_group_run(procedure_id: str, payload: ProcedureBatchPayload, request: Request) -> dict:
    """Corrige em lote todos os hosts que ainda possuem o problema escolhido."""

    _require_mutation(request)
    try:
        return request_procedure_batch(
            procedure_id,
            sites=payload.sites,
            operator=_operator_name(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"não foi possível enfileirar o lote: {type(exc).__name__}: {exc}",
        ) from exc
