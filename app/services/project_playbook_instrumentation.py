from __future__ import annotations

from typing import Any

from app.services import dynamic_agent as engine
from app.services import intelligent_agent


_INSTALLED = False
_PROJECT_PLAYBOOK_IDS = {
    "project-linux-prod-std",
    "project-linux-monitoring",
    "project-management-interface",
    "project-firewall",
    "project-windows",
    "network-dns-vpn-resolution",
}


def _playbook_id(playbook: Any) -> str:
    return str(getattr(playbook, "id", "") or "").strip()


def install_project_playbook_instrumentation() -> None:
    """Faz playbooks de projeto virarem execução, não uma lista para copiar.

    O raciocínio inteligente pode manter playbooks comuns em modo consultivo,
    porém os playbooks usados pela tela de Projetos representam um procedimento
    operacional que o usuário pediu para automatizar. Para esses IDs, os passos
    de leitura do YAML são renderizados e executados pelo motor normalmente.

    Correções continuam fora de ``steps`` e seguem as políticas de
    ``allowed_corrections``/revisão/aprovação existentes.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    previous_render = engine.render_steps
    raw_render = intelligent_agent._ORIGINAL_RENDER_STEPS

    def project_aware_render(playbook: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        if _playbook_id(playbook) in _PROJECT_PLAYBOOK_IDS:
            return raw_render(playbook, context)
        return previous_render(playbook, context)

    engine.render_steps = project_aware_render
    _INSTALLED = True
