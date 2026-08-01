from __future__ import annotations

from app.services import dynamic_agent
from app.services.operational_tools import execute_operational_tool, is_operational_tool


_INSTALLED = False


def install_operational_tools() -> None:
    """Integra o catálogo operacional sem alterar a política corretiva existente."""
    global _INSTALLED
    if _INSTALLED:
        return

    base_is_adaptive = dynamic_agent.is_adaptive_tool
    base_execute_adaptive = dynamic_agent.execute_adaptive_tool

    def is_adaptive_or_operational(name: str) -> bool:
        return is_operational_tool(name) or base_is_adaptive(name)

    def execute_adaptive_or_operational(executor, environment, name, arguments=None):
        if is_operational_tool(name):
            return execute_operational_tool(
                executor,
                environment,
                name,
                arguments,
            )
        return base_execute_adaptive(executor, environment, name, arguments)

    dynamic_agent.is_adaptive_tool = is_adaptive_or_operational
    dynamic_agent.execute_adaptive_tool = execute_adaptive_or_operational
    _INSTALLED = True
