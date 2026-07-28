from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request

from app.core.settings import get_settings
from app.services.opencode_cli import opencode_status
from app.web import _require_access


router = APIRouter(tags=["interface-tools"])


@router.get("/ui/api/tools/opencode")
def opencode_tool_status(request: Request) -> dict[str, Any]:
    """Expõe somente metadados públicos da integração, nunca tokens ou senha."""
    _require_access(request)
    return asdict(opencode_status(get_settings()))
