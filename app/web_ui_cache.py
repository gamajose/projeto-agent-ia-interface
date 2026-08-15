from __future__ import annotations

import importlib.metadata
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web import _require_access


router = APIRouter(tags=["interface-cache"])
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_UI_DIR = Path(__file__).resolve().parent / "ui"


def _project_version() -> str:
    """Lê primeiro a versão do checkout atual."""

    try:
        pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    try:
        return importlib.metadata.version("agent-ia-infra")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _git_revision() -> str:
    """Adiciona o commit atual à chave de cache quando o deploy é um clone Git."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _asset_version() -> str:
    version = _project_version()
    revision = _git_revision()
    return f"{version}-{revision}" if revision else version


_ASSET_VERSION = _asset_version()


def _inject_style(content: str, filename: str, *, marker: str | None = None) -> str:
    if filename in content:
        return content
    marker_attr = f' data-{marker}="1"' if marker else ""
    tag = f'<link rel="stylesheet" href="/ui/assets/{filename}?v={_ASSET_VERSION}"{marker_attr}>'
    return content.replace("</head>", f"  {tag}\n</head>")


def _inject_script(content: str, filename: str, *, marker: str | None = None, defer: bool = False) -> str:
    if filename in content:
        return content
    marker_attr = f' data-{marker}="1"' if marker else ""
    defer_attr = " defer" if defer else ""
    tag = f'<script src="/ui/assets/{filename}?v={_ASSET_VERSION}"{marker_attr}{defer_attr}></script>'
    return content.replace("</body>", f"  {tag}\n</body>")


def _inject_top_navigation_shell(content: str) -> str:
    if '<body class="top-navigation-layout">' in content:
        return content
    return content.replace("<body>", '<body class="top-navigation-layout">', 1)


def _inject_n2_shell(content: str) -> str:
    if 'data-view="n2"' not in content:
        nav_button = (
            '<button class="nav-item" data-view="n2" title="Documentação N2 com IA">'
            '<span class="nav-icon">▤</span><span>N2</span></button>'
        )
        content, count = re.subn(
            r'(<button class="nav-item" data-view="projects"[^>]*>.*?</button>)',
            rf'\1\n        {nav_button}',
            content,
            count=1,
            flags=re.DOTALL,
        )
        if not count:
            content = content.replace("</nav>", f"  {nav_button}\n      </nav>", 1)

    if 'id="view-n2"' not in content:
        shell = '<section class="view n2-page" id="view-n2" data-n2-shell="1"></section>'
        marker = '<section class="view" id="view-opencode">'
        if marker in content:
            content = content.replace(marker, f"{shell}\n\n      {marker}", 1)
        else:
            content = content.replace("</main>", f"  {shell}\n    </main>", 1)
    return content


def _inject_topology_assets(content: str) -> str:
    content = _inject_style(content, "topology.css")
    content = _inject_script(content, "topology.js")
    return content


def _inject_operator_assets(content: str) -> str:
    content = _inject_style(content, "operator-experience.css")
    content = _inject_script(content, "ui-core.js")
    content = _inject_script(content, "operator-experience.js")
    return content


def _inject_adaptive_assets(content: str) -> str:
    return _inject_script(content, "adaptive-analysis.js")


def _inject_operator_refresh_assets(content: str) -> str:
    for asset in (
        "navigation-refresh.css",
        "fleet-scope.css",
        "execution-visibility.css",
    ):
        content = _inject_style(content, asset)
    for asset in (
        "navigation-refresh.js",
        "fleet-scope.js",
        "execution-visibility.js",
    ):
        content = _inject_script(content, asset)
    return content


def _inject_noc_extension_assets(content: str) -> str:
    """Acopla NOC, N2 e a camada operacional compacta."""

    for asset, marker in (
        ("fleet-ui.css", "fleet-ui"),
        ("noc-automation.css", "noc-automation"),
        ("noc-agents-control.css", "noc-agents-control"),
        ("n2-workspace.css", "n2-workspace"),
        ("n2-persistence.css", "n2-persistence"),
        ("compact-operations.css", "compact-operations"),
        ("operational-refinement.css", "operational-refinement"),
        ("icon-actions.css", "icon-actions"),
        ("noc-memory-ui-v146.css", "noc-memory-ui-v146"),
        ("noc-selected-progress-v1465.css", "noc-selected-progress-v1465"),
        ("noc-manual-modal-v1468.css", "noc-manual-modal-v1468"),
        ("noc-problem-batch-v1470.css", "noc-problem-batch-v1470"),
    ):
        content = _inject_style(content, asset, marker=marker)

    for asset, marker in (
        ("fleet-ui.js", "fleet-ui"),
        ("noc-agents-control.js", "noc-agents-control"),
        ("n2-documentation.js", "n2-documentation"),
        ("navigation-policy.js", "navigation-policy"),
        ("compact-operations.js", "compact-operations"),
        ("compact-noc-layout.js", "compact-noc-layout"),
        ("noc-metric-modals.js", "noc-metric-modals"),
        ("noc-queue-controls.js", "noc-queue-controls"),
        ("noc-queue-pagination.js", "noc-queue-pagination"),
        ("noc-skills-manager.js", "noc-skills-manager"),
        ("noc-resolved-detail.js", "noc-resolved-detail"),
        ("inventory-pagination.js", "inventory-pagination"),
        ("operational-refinement.js", "operational-refinement"),
        ("playbook-manager-v2.js", "playbook-manager-v2"),
        ("icon-actions.js", "icon-actions"),
        ("noc-memory-ui-v146.js", "noc-memory-ui-v146"),
        ("noc-selected-progress-v1465.js", "noc-selected-progress-v1465"),
        ("noc-manual-modal-v1468.js", "noc-manual-modal-v1468"),
        ("noc-problem-batch-v1470.js", "noc-problem-batch-v1470"),
    ):
        content = _inject_script(content, asset, marker=marker, defer=True)
    return content


@router.get("/ui", include_in_schema=False, response_class=HTMLResponse)
@router.get("/ui/", include_in_schema=False, response_class=HTMLResponse)
def versioned_interface(request: Request) -> HTMLResponse:
    _require_access(request)
    content = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    content = _inject_top_navigation_shell(content)
    content = re.sub(r"([?&]v=)[0-9]+(?:\.[0-9]+){1,3}", rf"\g<1>{_ASSET_VERSION}", content)
    content = _inject_n2_shell(content)
    content = _inject_topology_assets(content)
    content = _inject_operator_assets(content)
    content = _inject_adaptive_assets(content)
    content = _inject_operator_refresh_assets(content)
    content = _inject_noc_extension_assets(content)
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Agent-UI-Version": _ASSET_VERSION,
        },
    )
