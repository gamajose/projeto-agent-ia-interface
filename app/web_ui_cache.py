from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web import _require_access


router = APIRouter(tags=["interface-cache"])
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_UI_DIR = Path(__file__).resolve().parent / "ui"


def _asset_version() -> str:
    try:
        return importlib.metadata.version("agent-ia-infra")
    except importlib.metadata.PackageNotFoundError:
        pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
        return match.group(1) if match else "dev"


_ASSET_VERSION = _asset_version()


def _inject_topology_assets(content: str) -> str:
    stylesheet = f'<link rel="stylesheet" href="/ui/assets/topology.css?v={_ASSET_VERSION}">'
    script = f'<script src="/ui/assets/topology.js?v={_ASSET_VERSION}"></script>'
    if "topology.css" not in content:
        content = content.replace("</head>", f"  {stylesheet}\n</head>")
    if "topology.js" not in content:
        content = content.replace("</body>", f"  {script}\n</body>")
    return content


def _inject_operator_assets(content: str) -> str:
    stylesheet = f'<link rel="stylesheet" href="/ui/assets/operator-experience.css?v={_ASSET_VERSION}">'
    core_script = f'<script src="/ui/assets/ui-core.js?v={_ASSET_VERSION}"></script>'
    experience_script = f'<script src="/ui/assets/operator-experience.js?v={_ASSET_VERSION}"></script>'
    if "operator-experience.css" not in content:
        content = content.replace("</head>", f"  {stylesheet}\n</head>")
    if "ui-core.js" not in content:
        content = content.replace("</body>", f"  {core_script}\n</body>")
    if "operator-experience.js" not in content:
        content = content.replace("</body>", f"  {experience_script}\n</body>")
    return content


def _inject_adaptive_assets(content: str) -> str:
    script = f'<script src="/ui/assets/adaptive-analysis.js?v={_ASSET_VERSION}"></script>'
    if "adaptive-analysis.js" not in content:
        content = content.replace("</body>", f"  {script}\n</body>")
    return content


@router.get("/ui", include_in_schema=False, response_class=HTMLResponse)
@router.get("/ui/", include_in_schema=False, response_class=HTMLResponse)
def versioned_interface(request: Request) -> HTMLResponse:
    _require_access(request)
    content = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    content = re.sub(r"([?&]v=)[0-9]+(?:\.[0-9]+){1,3}", rf"\g<1>{_ASSET_VERSION}", content)
    content = _inject_topology_assets(content)
    content = _inject_operator_assets(content)
    content = _inject_adaptive_assets(content)
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Agent-UI-Version": _ASSET_VERSION,
        },
    )
