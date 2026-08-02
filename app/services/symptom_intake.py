from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from app.core.settings import get_settings


_CURRENT_SYMPTOM: ContextVar[dict[str, Any] | None] = ContextVar(
    "agent_reported_symptom",
    default=None,
)

_STATE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stopped", ("parado", "parada", "stopped", "inactive", "inativo", "failed", "falhou")),
    ("down", ("down", "fora do ar", "indisponível", "indisponivel", "unreachable")),
    ("unhealthy", ("unhealthy", "não saudável", "nao saudavel", "health critical")),
    ("timeout", ("timeout", "timed out", "tempo esgotado", "sem resposta")),
    ("connection_refused", ("connection refused", "conexão recusada", "conexao recusada")),
    ("critical", ("critical", "crítico", "critico")),
    ("degraded", ("degraded", "degradado", "parcialmente operacional", "partially running")),
    ("resource_exhausted", ("filesystem cheio", "disco cheio", "100%", "swap alta", "memória alta", "memoria alta")),
)

_COMPONENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:servi[cç]o|processo|process|service|sensor|componente)\s+"
        r"(?P<component>[A-Za-z0-9_.@:/-]{2,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<component>[A-Za-z0-9_.@:/-]{2,120})\s+"
        r"(?:est[aá]\s+)?(?:parad[oa]|stopped|inactive|failed|down|unhealthy|critical)",
        re.IGNORECASE,
    ),
)

_KNOWN_COMPONENTS = (
    "automation-helper",
    "check-mk-agent",
    "check_mk_agent",
    "xinetd",
    "snmpd",
    "bsnmpd",
    "sshd",
    "docker",
    "openvpn",
    "ipsec",
    "dpinger",
)


def _compact(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _reported_state(text: str) -> str | None:
    lowered = text.casefold()
    for state, patterns in _STATE_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return state
    return None


def _component(text: str) -> str | None:
    lowered = text.casefold()
    for known in _KNOWN_COMPONENTS:
        if known in lowered:
            return known
    for pattern in _COMPONENT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = _compact(match.group("component"), 120).strip(" .,:;()[]")
        if value:
            return value
    return None


def parse_reported_symptom(objective: str) -> dict[str, Any]:
    statement = _compact(objective, 2000)
    state = _reported_state(statement)
    component = _component(statement)
    reported = bool(state or component)
    label = component or "componente alertado"
    state_label = state or "estado anormal informado"
    return {
        "reported": reported,
        "source": "operator_alert" if reported else "operator_objective",
        "statement": statement,
        "component": component,
        "reported_state": state,
        "accepted_as_starting_observation": reported,
        "root_cause_known": False,
        "investigation_question": (
            f"Por que {label} chegou ao estado {state_label} e o que impede sua recuperação?"
            if reported
            else "Qual falha explica o problema informado e qual recuperação é segura?"
        ),
        "do_not_repeat_as_root_cause": True,
    }


@contextmanager
def use_reported_symptom(objective: str) -> Iterator[dict[str, Any]]:
    contract = parse_reported_symptom(objective)
    token = _CURRENT_SYMPTOM.set(contract)
    try:
        yield contract
    finally:
        _CURRENT_SYMPTOM.reset(token)


def current_reported_symptom() -> dict[str, Any] | None:
    value = _CURRENT_SYMPTOM.get()
    return dict(value) if value else None


def enrich_reasoning_prompt(prompt: str, purpose: str) -> str:
    symptom = current_reported_symptom()
    if not symptom or not symptom.get("reported"):
        return prompt
    statement = symptom.get("statement") or ""
    component = symptom.get("component") or "componente alertado"
    state = symptom.get("reported_state") or "estado anormal"
    guidance = f"""
CONTEXTO OBRIGATÓRIO DO ALERTA
- Sintoma informado: {statement}
- Componente: {component}
- Estado informado: {state}
- Aceite o estado informado como ponto inicial operacional. Não desperdice rodadas apenas para provar novamente que o alerta existe.
- Revalide esse estado somente quando houver conflito de identidade do alvo, evidência contraditória ou durante a pós-validação da correção.
- O sintoma não é a causa raiz. Investigue por que o componente chegou a esse estado, quais dependências ou mudanças provocaram a falha e o que impede sua recuperação.
- Nunca use “{component} está {state}” como causa provável sem explicar o mecanismo anterior que produziu esse estado.
- A conclusão deve separar: sintoma recebido, causa raiz confirmada ou provável, cadeia causal, correção recomendada e critérios de recuperação.
- Durante planejamento de correção, antecipe falhas possíveis da própria ação e as evidências necessárias para replanejar com segurança.
ETAPA COGNITIVA: {purpose}
""".strip()
    return guidance + "\n\n" + prompt


def _same_as_symptom(cause: str, symptom: dict[str, Any]) -> bool:
    lowered = _compact(cause, 1000).casefold()
    component = str(symptom.get("component") or "").casefold()
    state = str(symptom.get("reported_state") or "").casefold()
    if not lowered:
        return True
    if component and component in lowered:
        symptom_words = {
            "parado", "parada", "stopped", "inactive", "failed", "falhou",
            "down", "unhealthy", "critical", "indisponível", "indisponivel",
        }
        meaningful = [word for word in re.findall(r"[a-z0-9_.@:-]{3,}", lowered) if word not in symptom_words]
        return meaningful == [component] or (len(meaningful) <= 2 and state in lowered)
    return False


def _recovery_scope(result: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    analysis = dict(result.get("analysis") or {})
    allowed: list[str] = []
    for name in (result.get("playbook") or {}).get("allowed_corrections") or []:
        value = str(name or "").strip()
        if value and value not in allowed:
            allowed.append(value)
    for item in analysis.get("proposed_actions") or []:
        value = str(item.get("tool") or "").strip()
        if value and value not in allowed:
            allowed.append(value)
    return {
        "target": result.get("target"),
        "environment": (result.get("environment_classification") or {}).get("environment"),
        "allowed_correction_tools": allowed,
        "max_recovery_rounds": settings.agent_recovery_max_rounds,
        "max_correction_actions": settings.agent_recovery_max_actions,
        "max_diagnostic_tools_per_round": settings.agent_recovery_max_diagnostics_per_round,
        "same_target_only": True,
        "database_access": False,
        "server_reboot": False,
        "container_lifecycle": False,
        "firewall_change": False,
        "package_change": False,
    }


def enrich_result_with_symptom(result: dict[str, Any], symptom: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    cause = _compact(analysis.get("probable_cause"), 2000)
    critic = dict(analysis.get("critic") or {})
    incident = dict(analysis.get("incident_intelligence") or {})
    validation = dict(incident.get("conclusion_validation") or {})
    evidence_map = [item for item in analysis.get("evidence_map") or [] if isinstance(item, dict)]

    cause_is_symptom = bool(symptom.get("reported") and _same_as_symptom(cause, symptom))
    supported = (
        not cause_is_symptom
        and bool(cause)
        and str(critic.get("verdict") or "") == "accept"
        and str(validation.get("verdict") or "") != "contradicted"
        and any(str(item.get("evidence") or "").strip() for item in evidence_map)
    )
    if cause_is_symptom:
        root_cause_status = "unknown"
        root_cause = ""
        missing = list(analysis.get("missing_information") or [])
        missing.append(
            "O estado informado pelo alerta foi identificado, mas ainda falta explicar o mecanismo que causou a falha."
        )
        analysis["missing_information"] = list(dict.fromkeys(str(item) for item in missing if str(item).strip()))
    else:
        root_cause_status = "confirmed" if supported else "probable" if cause else "unknown"
        root_cause = cause

    component = symptom.get("component") or result.get("hostname") or result.get("target") or "componente"
    reported_state = symptom.get("reported_state") or "estado anormal"
    causal_chain: list[dict[str, str]] = []
    if root_cause:
        causal_chain.append({"type": "root_cause", "statement": root_cause})
    causal_chain.append(
        {
            "type": "reported_symptom",
            "statement": str(symptom.get("statement") or f"{component}: {reported_state}"),
        }
    )

    analysis["symptom_contract"] = symptom
    analysis["root_cause"] = {
        "status": root_cause_status,
        "statement": root_cause,
        "symptom_was_not_used_as_cause": not cause_is_symptom,
        "investigation_question": symptom.get("investigation_question"),
        "causal_chain": causal_chain,
    }
    analysis["recovery_goal"] = {
        "component": component,
        "from_state": reported_state,
        "objective": f"Restaurar {component} e remover a causa que provocou {reported_state}.",
        "success_criteria": [
            f"{component} permanece no estado operacional esperado.",
            "As dependências relacionadas ao incidente permanecem saudáveis.",
            "Nenhum novo erro bloqueador aparece nas validações posteriores.",
            "O alerta pode normalizar sem reaparecer durante a observação.",
        ],
    }
    analysis["recovery_scope"] = _recovery_scope({**result, "analysis": analysis})
    result["symptom_contract"] = symptom
    result["analysis"] = analysis
    return result
