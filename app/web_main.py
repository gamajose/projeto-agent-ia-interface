from __future__ import annotations

import os

import uvicorn

from app.main import app
from app.web import register_ui
from app.web_batch import router as batch_router
from app.web_tools import router as tools_router


register_ui(app)
if not getattr(app.state, "agent_ui_batch_registered", False):
    app.include_router(batch_router)
    app.state.agent_ui_batch_registered = True
if not getattr(app.state, "agent_ui_tools_registered", False):
    app.include_router(tools_router)
    app.state.agent_ui_tools_registered = True


def main() -> None:
    uvicorn.run(
        "app.web_main:app",
        host=os.getenv("AGENT_UI_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_UI_PORT", "8080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
