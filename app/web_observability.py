from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.services.execution_store import get_execution_store
from app.services.metrics import render_prometheus, snapshot
from app.services.performance_config import get_performance_config
from app.web import _require_access


router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
def prometheus_metrics(request: Request) -> PlainTextResponse:
    _require_access(request)
    return PlainTextResponse(
        render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ui/api/observability")
def observability_summary(request: Request) -> dict:
    _require_access(request)
    config = get_performance_config()
    return {
        "execution_store": get_execution_store().backend_name(),
        "sse_enabled": config.sse_enabled,
        "metrics_enabled": config.metrics_enabled,
        "budgets": {
            "commands": config.max_total_commands,
            "ai_calls": config.max_total_ai_calls,
            "investigation_seconds": config.max_investigation_seconds,
            "host_seconds": config.max_host_seconds,
            "deep_dive_hosts": config.max_deep_dive_hosts,
        },
        "metrics": snapshot(),
    }
