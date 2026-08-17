from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.noc_problem_batch import current_problem_groups, problem_group_detail
from app.services.noc_problem_batch_dispatch import request_procedure_batch
from app.web import _operator_name, _require_access, _require_mutation


logger = logging.getLogger(__name__)
router = APIRouter(tags=["interface-noc-batch"])


class ProcedureBatchPayload(BaseModel):
    sites: list[str] = Field(default_factory=list, max_length=1000)


@router.get("/ui/api/noc/problem-groups")
def noc_problem_groups(request: Request, refresh: bool = False) -> dict:
    """Agrupa os alertas persistidos; refresh força uma nova fotografia global."""

    _require_access(request)
    try:
        return current_problem_groups(refresh=refresh)
    except Exception as exc:
        logger.exception("Falha ao atualizar grupos de problemas do NOC")
        raise HTTPException(
            status_code=503,
            detail="não foi possível atualizar a fotografia do Checkmk. O detalhe técnico foi registrado no serviço web.",
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
        logger.exception("Falha ao abrir detalhe do procedure NOC %s", procedure_id)
        raise HTTPException(
            status_code=503,
            detail="não foi possível abrir os hosts deste problema. O detalhe técnico foi registrado no serviço web.",
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
        logger.exception("Falha ao enfileirar lote NOC %s", procedure_id)
        raise HTTPException(
            status_code=503,
            detail="não foi possível enfileirar a correção. O detalhe técnico foi registrado no serviço web.",
        ) from exc
