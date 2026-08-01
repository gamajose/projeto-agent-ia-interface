from __future__ import annotations

import os

import uvicorn

from app.main import app
from app.services.ai_instrumentation import install_ai_instrumentation
from app.services.operational_tool_instrumentation import install_operational_tools
from app.services.multi_host_instrumentation import install_multi_host_instrumentation

install_ai_instrumentation()
install_operational_tools()
install_multi_host_instrumentation()

from app.web import register_ui
from app.web_batch import router as batch_router
from app.web_executions import router as executions_router
from app.web_flow import router as flow_router
from app.web_incidents import router as incidents_router
from app.web_observability import router as observability_router
from app.web_operator_experience import router as operator_experience_router
from app.web_playbooks import router as playbooks_router
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
if not getattr(app.state, "agent_ui_flow_registered", False):
    app.include_router(flow_router)
    app.state.agent_ui_flow_registered = True
if not getattr(app.state, "agent_ui_incidents_registered", False):
    app.include_router(incidents_router)
    app.state.agent_ui_incidents_registered = True
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


def main() -> None:
    uvicorn.run(
        "app.web_main:app",
        host=os.getenv("AGENT_UI_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_UI_PORT", "8080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
