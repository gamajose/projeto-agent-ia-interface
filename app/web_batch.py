from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.services.batch_manifest import BatchManifestError, parse_batch_manifest
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-batch"])


class BatchManifestPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


@router.get("/ui/api/batches/config")
def batch_config(request: Request) -> dict[str, Any]:
    _require_access(request)
    settings = get_settings()
    return {
        "enabled": settings.agent_batch_enabled,
        "max_targets": settings.agent_batch_max_targets,
        "concurrency": settings.agent_batch_concurrency,
        "max_file_bytes": settings.agent_batch_max_file_bytes,
        "formats": ["txt", "csv", "json", "yaml", "yml"],
    }


@router.post("/ui/api/batches/parse")
def parse_batch(payload: BatchManifestPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    if not settings.agent_batch_enabled:
        raise HTTPException(status_code=404, detail="execução em lote desabilitada")

    size = len(payload.content.encode("utf-8"))
    if size > settings.agent_batch_max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"arquivo excede o limite de {settings.agent_batch_max_file_bytes} bytes"
            ),
        )

    try:
        result = parse_batch_manifest(
            payload.filename,
            payload.content,
            max_targets=settings.agent_batch_max_targets,
        )
    except BatchManifestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        **result,
        "limits": {
            "max_targets": settings.agent_batch_max_targets,
            "concurrency": settings.agent_batch_concurrency,
            "max_file_bytes": settings.agent_batch_max_file_bytes,
        },
    }
