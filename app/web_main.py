from __future__ import annotations

import os

import uvicorn
from fastapi import Request
from fastapi.responses import Response

from app.main import app
from app.services.ai_instrumentation import install_ai_instrumentation
from app.services.codex_provider_instrumentation import install_codex_provider_preflight
from app.services.ensemble_instrumentation import install_ensemble_reasoning
from app.services.operational_tool_instrumentation import install_operational_tools
from app.services.multi_host_instrumentation import install_multi_host_instrumentation
from app.services.project_playbook_instrumentation import install_project_playbook_instrumentation

install_ai_instrumentation()
install_operational_tools()
install_multi_host_instrumentation()
install_ensemble_reasoning()
install_project_playbook_instrumentation()
install_codex_provider_preflight()

from app.web import register_ui
from app.web_batch import router as batch_router
from app.web_executions import router as executions_router
from app.web_fleet import router as fleet_router
from app.web_flow import router as flow_router
from app.web_incidents import router as incidents_router
from app.web_observability import router as observability_router
from app.web_operator_experience import router as operator_experience_router
from app.web_playbooks import router as playbooks_router
from app.web_projects import router as projects_router
from app.web_replay import router as replay_router
from app.web_settings import enable_dynamic_provider_payload, router as settings_router
from app.web_tools import router as tools_router
from app.web_topology import router as topology_router
from app.web_ui_cache import router as ui_cache_router


enable_dynamic_provider_payload()
if not getattr(app.state, "agent_ui_cache_registered", False):
    app.include_router(ui_cache_router)
    app.state.agent_ui_cache_registered = True
register_ui(app)
if not getattr(app.state, "agent_ui_batch_registered", False):
    app.include_router(batch_router)
    app.state.agent_ui_batch_registered = True
if not getattr(app.state, "agent_ui_tools_registered", False):
    app.include_router(tools_router)
    app.state.agent_ui_tools_registered = True
if not getattr(app.state, "agent_ui_settings_registered", False):
    app.include_router(settings_router)
    app.state.agent_ui_settings_registered = True
if not getattr(app.state, "agent_ui_executions_registered", False):
    app.include_router(executions_router)
    app.state.agent_ui_executions_registered = True
if not getattr(app.state, "agent_ui_playbooks_registered", False):
    app.include_router(playbooks_router)
    app.state.agent_ui_playbooks_registered = True
if not getattr(app.state, "agent_ui_projects_registered", False):
    app.include_router(projects_router)
    app.state.agent_ui_projects_registered = True
if not getattr(app.state, "agent_ui_flow_registered", False):
    app.include_router(flow_router)
    app.state.agent_ui_flow_registered = True
if not getattr(app.state, "agent_ui_incidents_registered", False):
    app.include_router(incidents_router)
    app.state.agent_ui_incidents_registered = True
if not getattr(app.state, "agent_ui_fleet_registered", False):
    app.include_router(fleet_router)
    app.state.agent_ui_fleet_registered = True
if not getattr(app.state, "agent_ui_topology_registered", False):
    app.include_router(topology_router)
    app.state.agent_ui_topology_registered = True
if not getattr(app.state, "agent_observability_registered", False):
    app.include_router(observability_router)
    app.state.agent_observability_registered = True
if not getattr(app.state, "agent_replay_registered", False):
    app.include_router(replay_router)
    app.state.agent_replay_registered = True
if not getattr(app.state, "agent_operator_experience_registered", False):
    app.include_router(operator_experience_router)
    app.state.agent_operator_experience_registered = True


@app.middleware("http")
async def inject_fleet_ui_assets(request: Request, call_next):
    """Acopla o painel de descoberta sem alterar o fluxo da Nova análise."""
    response = await call_next(request)
    if request.url.path not in {"/ui", "/ui/"}:
        return response
    content_type = str(response.headers.get("content-type") or "")
    if "text/html" not in content_type.casefold() or not hasattr(response, "body_iterator"):
        return response

    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    body = b"".join(chunks).decode("utf-8", errors="replace")
    marker = '<script src="/ui/assets/fleet-ui.js?v=1.34.0" defer></script>'
    if marker not in body:
        body = body.replace("</body>", f"  {marker}\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(
        content=body,
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
    )


def main() -> None:
    uvicorn.run(
        "app.web_main:app",
        host=os.getenv("AGENT_UI_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_UI_PORT", "8080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
