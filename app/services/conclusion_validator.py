from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _observed_values(result: dict[str, Any]) -> str:
    values: list[str] = []
    analysis = dict(result.get("analysis") or {})
    values.extend(_text(item) for item in analysis.get("facts") or [])
    for item in result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        values.extend(
            (
                _text(item.get("command")),
                _text(item.get("stdout")),
                _text(item.get("stderr")),
                _text(item.get("reason")),
            )
        )
    return "\n".join(value for value in values if value).casefold()


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def validate_conclusion(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    claim = " ".join(
        filter(None, (_text(analysis.get("probable_cause")), _text(analysis.get("conclusion"))))
    ).casefold()
    observed = _observed_values(result)
    journey = [
        item
        for item in analysis.get("access_journey")
        or (result.get("connection") or {}).get("access_journey")
        or []
        if isinstance(item, dict)
    ]
    shell_completed = any(
        item.get("step") == "target_shell" and item.get("status") == "completed"
        for item in journey
    )
    contradictions: list[str] = []
    checks: list[dict[str, Any]] = []

    vpn_claim = _matches(
        (
            r"\bvpn\b.{0,28}\b(?:indispon[ií]vel|caiu|fora|inativa|falhou|com falha)\b",
            r"\bfalha\b.{0,20}\bna\s+vpn\b",
            r"\brota\s+vpn\b.{0,20}\b(?:ausente|indispon[ií]vel|inexistente)\b",
        ),
        claim,
    )
    if vpn_claim:
        passed = not shell_completed
        checks.append({"check": "A causa atribuída à VPN é compatível com o caminho de acesso.", "passed": passed})
        if not passed:
            contradictions.append("O shell do destino foi alcançado; a VPN não pode ser tratada como causa comprovada desta execução.")

    container_stopped_claim = _matches(
        (
            r"\bcontainer\b.{0,24}\b(?:parado|caiu|indispon[ií]vel|stopped|down)\b",
            r"\bdocker\b.{0,24}\b(?:parado|caiu|indispon[ií]vel|stopped|down)\b",
        ),
        claim,
    )
    if container_stopped_claim:
        container_up = bool(re.search(r"\bup\s+\d|\bup\s+about|\(unhealthy\)|\bstatus[=: ]+running\b", observed))
        checks.append({"check": "A afirmação de container parado é compatível com o estado coletado.", "passed": not container_up})
        if container_up:
            contradictions.append("A evidência mostra o container ativo; o problema pode estar no healthcheck ou em um processo interno.")

    authentication_claim = _matches(
        (
            r"\bautentica(?:ç|c)[aã]o\b",
            r"\bauthentication\b",
            r"\bsenha\b.{0,16}\b(?:incorreta|inv[aá]lida|rejeitada)\b",
            r"\bcredencial\b.{0,16}\b(?:incorreta|inv[aá]lida|rejeitada|expirada)\b",
        ),
        claim,
    )
    if authentication_claim:
        checks.append({"check": "A hipótese de autenticação é compatível com a abertura do shell.", "passed": not shell_completed})
        if shell_completed:
            contradictions.append("O shell foi validado; uma falha de autenticação não explica o incidente técnico analisado.")

    service_stopped_claim = _matches(
        (
            r"\bservi[cç]o\b.{0,24}\b(?:parado|inativo|stopped|inactive)\b",
            r"\bprocesso\b.{0,24}\b(?:parado|inativo|stopped|inactive)\b",
        ),
        claim,
    )
    service_running = bool(
        re.search(r"\bactive\s*\(running\)|activestate=active|substate=running|\b(?:servi[cç]o|processo)\b.{0,20}\brunning\b", observed)
    )
    if service_stopped_claim and service_running:
        contradictions.append("Há evidência de serviço ativo/running que precisa ser reconciliada com a causa declarada.")
        checks.append({"check": "O serviço declarado como parado não aparece ativo nas evidências.", "passed": False})

    evidence_map = [item for item in analysis.get("evidence_map") or [] if isinstance(item, dict)]
    facts = [item for item in analysis.get("facts") or [] if _text(item)]
    supported = bool(evidence_map) and bool(facts)
    if contradictions:
        verdict = "contradicted"
        recommendation = "Revisar a causa provável antes de propor qualquer ação corretiva."
    elif supported:
        verdict = "supported"
        recommendation = "A conclusão possui fatos e mapa de evidências; manter a pós-validação antes de encerrar."
    else:
        verdict = "needs_more_evidence"
        recommendation = "Coletar uma evidência independente que confirme ou refute a causa provável."

    return {
        "verdict": verdict,
        "supported": supported and not contradictions,
        "checks": checks,
        "contradictions": contradictions,
        "recommendation": recommendation,
    }
