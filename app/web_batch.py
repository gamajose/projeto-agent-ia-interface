from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.batch_manifest import BatchManifestError, parse_batch_manifest
from app.services.jobs import enqueue_investigation
from app.services.provider_router import ProviderResolution, resolve_automatic_provider
from app.services.runner import run_target
from app.web import (
    InvestigationPayload,
    _compact_result,
    _operator_name,
    _require_access,
    _require_mutation,
    _validate_selection,
)


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
            detail=f"arquivo excede o limite de {settings.agent_batch_max_file_bytes} bytes",
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


def _resolve_batch_provider(
    provider: str,
    model: str | None,
) -> tuple[str, str | None, ProviderResolution | None]:
    if provider != "auto":
        return provider, model, None
    selection = resolve_automatic_provider(get_settings())
    return selection.provider, selection.model, selection


@router.post("/ui/api/batches/investigations")
def create_batch_investigation(
    payload: InvestigationPayload,
    request: Request,
) -> dict[str, Any]:
    """Executa um item do lote sem misturar o estado dos demais servidores.

    Quando a IA está em modo automático, o provedor é resolvido antes do job.
    Assim, o playbook importado continua disponível como contexto consultivo em
    vez de ser substituído pelo playbook automático do autopilot geral.
    """
    _require_mutation(request)
    settings = get_settings()
    if not settings.agent_batch_enabled:
        raise HTTPException(status_code=404, detail="execução em lote desabilitada")

    ensure_database_schema()
    provider, model, effective_mode = _validate_selection(payload, settings)
    execution_provider, execution_model, automatic_selection = _resolve_batch_provider(
        provider,
        model,
    )
    playbook_id = (payload.playbook_id or "").strip() or None

    common = {
        "environment": payload.environment,
        "mode": effective_mode,
        "approve": False,
        "ssh_port": payload.ssh_port,
        "provider_name": execution_provider,
        "model_name": execution_model,
        "playbook_mode": payload.playbook_mode,
        "playbook_id": playbook_id,
        "settings": settings,
    }

    metadata = {
        "source": "web_ui_batch",
        "operator": _operator_name(),
        "requested_mode": payload.mode,
        "requested_provider": provider,
        "autopilot": provider == "auto",
        "playbook_mode": payload.playbook_mode,
        "playbook_id": playbook_id,
    }

    if settings.agent_execution_mode.strip().casefold() == "queue":
        try:
            response = enqueue_investigation(
                payload.target.strip(),
                payload.objective.strip(),
                metadata=metadata,
                **common,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"fila indisponível: {type(exc).__name__}: {exc}",
            ) from exc
        if automatic_selection:
            response["provider_selection"] = automatic_selection.as_dict()
        return response

    try:
        result = run_target(
            payload.target.strip(),
            payload.objective.strip(),
            **common,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    if automatic_selection:
        result["provider_selection"] = automatic_selection.as_dict()
        result["selected_provider"] = automatic_selection.provider
        result["selected_model"] = automatic_selection.model
        automation = dict(result.get("automation") or {})
        automation.update(
            {
                "mode": "safe_batch_autopilot",
                "provider": automatic_selection.as_dict(),
                "playbook_selection": payload.playbook_mode,
            }
        )
        result["automation"] = automation

    compact = _compact_result(result)
    compact["requested_mode"] = payload.mode
    compact["requested_provider"] = provider
    compact["selected_provider"] = result.get("selected_provider") or execution_provider
    compact["selected_model"] = result.get("selected_model") or execution_model
    return compact
