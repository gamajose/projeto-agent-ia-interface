from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.services import dynamic_agent as engine
from app.services import intelligent_agent, symptom_intake
from app.services.symptom_intake import enrich_reasoning_prompt


_INSTALLED = False


def _normalized(value: Any) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()


def _tokens(value: str) -> list[str]:
    return [
        token.strip(".,;:!?()[]{}")
        for token in re.findall(r"[a-z0-9_.@:-]{2,}", value)
        if token.strip(".,;:!?()[]{}")
    ]


def _cause_only_repeats_symptom(cause: str, symptom: dict[str, Any]) -> bool:
    text = _normalized(cause)
    component = _normalized(symptom.get("component"))
    if not text:
        return True
    if component and component not in text:
        return False
    removable = {
        "o", "a", "os", "as", "um", "uma", "the", "service", "servico", "process",
        "processo", "component", "componente", "esta", "is", "ficou", "encontra", "se",
        "parado", "parada", "stopped", "inactive", "inativo", "inativa", "failed", "falhou",
        "down", "unhealthy", "critical", "critico", "critica", "indisponivel", "sem", "resposta",
        "timeout", "degraded", "degradado", "degradada",
    }
    component_tokens = set(_tokens(component))
    remaining = [
        token
        for token in _tokens(text)
        if token not in removable and token not in component_tokens
    ]
    return not remaining


def install_symptom_reasoning() -> None:
    """Instala as camadas de sintoma e hipóteses adaptativas uma única vez."""
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

    # A função pública de enriquecimento consulta o nome global em runtime;
    # substituir o comparador aqui corrige também resultados já persistidos.
    symptom_intake._same_as_symptom = _cause_only_repeats_symptom
    intelligent_agent.resilient_model_call = root_cause_model_call
    engine._model_call = root_cause_model_call
    _INSTALLED = True

    # Sinais determinísticos específicos são instalados antes do wrapper
    # cognitivo. Assim, erros diretos como "No space left on device" encerram
    # a disputa de hipóteses sem transformar alertas genéricos em certeza.
    from app.services.adaptive_hypothesis_certainty import install_certainty_rules
    from app.services.adaptive_reasoning import install_adaptive_reasoning

    install_certainty_rules()
    install_adaptive_reasoning()
