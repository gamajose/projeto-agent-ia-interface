from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.services.opencode_cli import (
    OpenCodeError,
    get_opencode_run,
    list_opencode_runs,
    opencode_status,
    submit_opencode_run,
)
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-tools"])


class OpenCodeRunPayload(BaseModel):
    prompt: str = Field(min_length=3, max_length=50000)
    agent: Literal["plan", "build"] = "plan"
    model: str | None = Field(default=None, max_length=255)
    session_id: str | None = Field(default=None, max_length=255)
    confirm_changes: bool = False


@router.get("/ui/api/tools/opencode")
def opencode_tool_status(request: Request) -> dict[str, Any]:
    """Expõe somente metadados públicos da integração, nunca tokens ou senha."""
    _require_access(request)
    return asdict(opencode_status(get_settings()))


@router.get("/ui/api/tools/opencode/runs")
def opencode_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    _require_access(request)
    items = list_opencode_runs(limit)
    return {"total": len(items), "items": items}


@router.get("/ui/api/tools/opencode/runs/{run_id}")
def opencode_run_detail(run_id: str, request: Request) -> dict[str, Any]:
    _require_access(request)
    result = get_opencode_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="execução do OpenCode não encontrada")
    return result


@router.post("/ui/api/tools/opencode/runs")
def create_opencode_run(payload: OpenCodeRunPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    if payload.agent == "build" and not payload.confirm_changes:
        raise HTTPException(
            status_code=422,
            detail="confirme explicitamente que o OpenCode pode editar arquivos e executar comandos no projeto",
        )
    try:
        return submit_opencode_run(
            payload.prompt,
            agent=payload.agent,
            model=payload.model,
            session_id=payload.session_id,
            auto_approve=payload.agent == "build" and payload.confirm_changes,
            settings=settings,
        )
    except OpenCodeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"não foi possível iniciar o OpenCode: {type(exc).__name__}: {exc}",
        ) from exc
