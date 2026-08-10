from __future__ import annotations

from app.services import adaptive_orchestrator, dynamic_agent
from app.services.noc_specialist_tools import (
    describe_noc_specialist_tools,
    execute_noc_specialist_tool,
    is_noc_specialist_tool,
)
from app.services.operational_tools import (
    describe_operational_tools,
    execute_operational_tool,
    is_operational_tool,
)


_INSTALLED = False


def install_operational_tools() -> None:
    """Integra catálogo operacional + especialistas NOC sem ampliar a política corretiva."""
    global _INSTALLED
    if _INSTALLED:
        return

    base_is_adaptive = dynamic_agent.is_adaptive_tool
    base_execute_adaptive = dynamic_agent.execute_adaptive_tool
    base_describe_operational = adaptive_orchestrator.describe_operational_tools

    def is_adaptive_or_operational(name: str) -> bool:
        return is_noc_specialist_tool(name) or is_operational_tool(name) or base_is_adaptive(name)

    def execute_adaptive_or_operational(executor, environment, name, arguments=None):
        if is_noc_specialist_tool(name):
            return execute_noc_specialist_tool(
                executor,
                environment,
                name,
                arguments,
            )
        if is_operational_tool(name):
            return execute_operational_tool(
                executor,
                environment,
                name,
                arguments,
            )
        return base_execute_adaptive(executor, environment, name, arguments)

    def describe_operational_with_specialists():
        rows = [*base_describe_operational(), *describe_noc_specialist_tools()]
        seen: set[str] = set()
        unique = []
        for item in rows:
            name = str(item.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            unique.append(item)
        return unique

    dynamic_agent.is_adaptive_tool = is_adaptive_or_operational
    dynamic_agent.execute_adaptive_tool = execute_adaptive_or_operational
    adaptive_orchestrator.describe_operational_tools = describe_operational_with_specialists
    _INSTALLED = True
