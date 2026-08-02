from __future__ import annotations

import re
from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction


_REQUIRED_RESTART_PATTERNS = (
    r"\breboot required\b",
    r"\brein[ií]cio (?:do host |da vm |do servidor )?(?:é |e )?(?:obrigat[oó]rio|necess[aá]rio)\b",
    r"\bnecess[aá]rio reiniciar (?:o host|a vm|o servidor|a m[aá]quina)\b",
    r"\brequer (?:um )?rein[ií]cio\b",
    r"/var/run/reboot-required",
    r"\bneeds-restarting\b.*\breboot\b",
    r"\bkernel instalado\b.*\bkernel em uso\b",
)

_RECOMMENDED_RESTART_PATTERNS = (
    r"\brecomenda-se reiniciar\b",
    r"\brein[ií]cio recomendado\b",
    r"\bpode exigir rein[ií]cio\b",
    r"\bap[oó]s (?:o )?reboot\b",
    r"\bjanela de (?:reboot|rein[ií]cio)\b",
)


def _compact(value: Any, limit: int = 2400) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _analysis_text(investigation: dict[str, Any]) -> str:
    analysis = dict(investigation.get("analysis") or {})
    parts: list[str] = [
        str(investigation.get("objective") or ""),
        str(analysis.get("summary") or ""),
        str(analysis.get("probable_cause") or ""),
        str(analysis.get("conclusion") or ""),
        str(analysis.get("next_safe_step") or ""),
    ]
    parts.extend(str(item) for item in analysis.get("recommendations") or [])
    parts.extend(str(item) for item in analysis.get("facts") or [])
    for item in investigation.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        parts.extend(
            [
                str(item.get("purpose") or ""),
                str(item.get("stdout") or "")[-1600:],
                str(item.get("stderr") or "")[-800:],
                str(item.get("reason") or ""),
            ]
        )
    return "\n".join(part for part in parts if part).strip()


def _matched_evidence(text: str, patterns: tuple[str, ...]) -> list[str]:
    rows: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 140)
        excerpt = _compact(text[start:end], 360)
        if excerpt and excerpt not in rows:
            rows.append(excerpt)
    return rows[:5]


def _service_restart(actions: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    for action in actions:
        arguments = dict(action.get("arguments") or {})
        operation = str(arguments.get("action") or "").strip().casefold()
        if operation not in {"start", "restart", "reload", "enable --now"}:
            continue
        target = arguments.get("unit") or arguments.get("service") or action.get("description") or action.get("tool")
        items.append(
            {
                "tool": str(action.get("tool") or ""),
                "target": str(target or "componente"),
                "operation": operation,
            }
        )
    return {
        "required": any(item["operation"] == "restart" for item in items),
        "items": items,
        "reason": (
            "A proposta contém reinício controlado de serviço ou componente."
            if any(item["operation"] == "restart" for item in items)
            else "A proposta não contém reinício de serviço; pode conter start, reload ou outra recuperação restrita."
        ),
    }


def assess_correction_readiness(
    investigation: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        environment = EnvironmentType(str(investigation.get("environment") or EnvironmentType.UNKNOWN.value))
    except ValueError:
        environment = EnvironmentType.UNKNOWN

    text = _analysis_text(investigation)
    required_evidence = _matched_evidence(text, _REQUIRED_RESTART_PATTERNS)
    recommended_evidence = _matched_evidence(text, _RECOMMENDED_RESTART_PATTERNS)
    if required_evidence:
        host_status = "required"
        host_reason = "As evidências indicam que a recuperação completa depende de reinício da máquina."
        evidence = required_evidence
    elif recommended_evidence:
        host_status = "recommended"
        host_reason = "A investigação recomenda reinício da máquina, mas as ações seguras podem ser tentadas antes."
        evidence = recommended_evidence
    else:
        host_status = "not_required"
        host_reason = "Nenhuma evidência demonstrou necessidade de reiniciar a VM ou o servidor."
        evidence = []

    automatic_correction = environment_allows_correction(environment)
    if environment in {EnvironmentType.PRODUCTION, EnvironmentType.STANDBY}:
        policy_message = "Produção e standby recebem proposta e validação, mas não alteração automática."
    elif environment == EnvironmentType.MONITORING:
        policy_message = "Ações restritas de monitoramento podem ser executadas após aprovação; reinício da máquina permanece manual."
    elif environment == EnvironmentType.TRAINING:
        policy_message = "Ações restritas podem ser executadas após aprovação; reinício da máquina permanece uma etapa manual confirmada pelo analista."
    else:
        policy_message = "O ambiente precisa ser classificado antes de qualquer alteração."

    return {
        "environment": environment.value,
        "actions_count": len(actions),
        "automatic_correction_allowed": automatic_correction,
        "service_restart": _service_restart(actions),
        "host_restart": {
            "status": host_status,
            "required": host_status == "required",
            "recommended": host_status == "recommended",
            "reason": host_reason,
            "evidence": evidence,
            "decision_required": host_status in {"required", "recommended"},
            "automatic_execution": False,
            "decline_behavior": "Executar as demais ações aprovadas sem reiniciar a máquina e realizar nova varredura.",
            "accept_behavior": "Registrar a necessidade de reinício manual e aguardar o analista confirmar que a máquina voltou antes da nova varredura.",
        },
        "policy_message": policy_message,
        "validation_plan": [
            "Executar somente as ações aprovadas para o mesmo alvo.",
            "Repetir verificações do serviço ou componente tratado.",
            "Comparar o estado anterior com o estado posterior.",
            "Se o problema permanecer, transformar o novo resultado em evidência e reanalisar a causa.",
        ],
    }
