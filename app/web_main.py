from __future__ import annotations

import os

import uvicorn

from app.main import app
from app.web import register_ui


register_ui(app)


def main() -> None:
    uvicorn.run(
        "app.web_main:app",
        host=os.getenv("AGENT_UI_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_UI_PORT", "8080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
