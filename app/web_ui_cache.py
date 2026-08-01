from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web import _require_access


router = APIRouter(tags=["interface-cache"])
_UI_DIR = Path(__file__).resolve().parent / "ui"
_ASSET_VERSION = importlib.metadata.version("agent-ia-infra")


@router.get("/ui", include_in_schema=False, response_class=HTMLResponse)
@router.get("/ui/", include_in_schema=False, response_class=HTMLResponse)
def versioned_interface(request: Request) -> HTMLResponse:
    _require_access(request)
    content = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    content = re.sub(r"([?&]v=)[0-9]+(?:\.[0-9]+){1,3}", rf"\g<1>{_ASSET_VERSION}", content)
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Agent-UI-Version": _ASSET_VERSION,
        },
    )
