from __future__ import annotations

from typing import Any

from app.services import dynamic_agent as engine
from app.services import intelligent_agent
from app.services.symptom_intake import enrich_reasoning_prompt


_INSTALLED = False


def install_symptom_reasoning() -> None:
    """Instala uma única camada contextual sobre as chamadas cognitivas.

    O ContextVar do sintoma mantém a implementação segura entre execuções
    concorrentes. A camada não altera prompts quando a investigação não veio de
    um alerta reconhecível.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    original = intelligent_agent.resilient_model_call

    def root_cause_model_call(
        prompt: str,
        purpose: str,
        provider_name: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        return original(
            enrich_reasoning_prompt(prompt, purpose),
            purpose,
            provider_name,
        )

    intelligent_agent.resilient_model_call = root_cause_model_call
    engine._model_call = root_cause_model_call
    _INSTALLED = True
