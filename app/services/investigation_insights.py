from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from app.core.settings import Settings, get_settings
from app.services.playbooks import get_playbook


def _unique_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _flatten_text(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict):
            result.extend(_flatten_text(*value.values()))
        elif isinstance(value, (list, tuple, set)):
            result.extend(_flatten_text(*value))
    return _unique_text(result)


def _clamp(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


def _analysis_from_case(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("analysis")
    return dict(value) if isinstance(value, dict) else {}


def _case_date(case: dict[str, Any]) -> str | None:
    value = case.get("created_at") or case.get("detected_at") or case.get("resolved_at")
    return str(value) if value else None


def _latest_date(cases: list[dict[str, Any]]) -> str | None:
    dated: list[tuple[datetime, str]] = []
    for case in cases:
        raw = _case_date(case)
        if not raw:
            continue
        try:
            dated.append((datetime.fromisoformat(raw.replace("Z", "+00:00")), raw))
        except ValueError:
            continue
    return max(dated, key=lambda item: item[0])[1] if dated else None


def _target_context(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    connection = dict(result.get("connection") or {})
    identity = dict(result.get("identity") or {})
    classification = dict(result.get("environment_classification") or {})
    inventory = dict(result.get("inventory") or {})
    client_name = str(
        connection.get("client_name")
        or inventory.get("hostname")
        or analysis.get("client_name")
        or ""
    ).strip()
    hostname = str(identity.get("hostname") or result.get("hostname") or "").strip()
    return {
        "client_name": client_name or None,
        "vpn_ip": connection.get("vpn_ip") or result.get("target"),
        "hostname": hostname or None,
        "environment": classification.get("environment") or result.get("environment") or "unknown",
        "profile": result.get("profile") or "unknown",
        "os_name": identity.get("os_name") or None,
        "access_mode": connection.get("mode") or "direct_ssh",
        "ssh_port": connection.get("ssh_port") or inventory.get("ssh_port"),
        "username": connection.get("username") or None,
        "is_pfsense": bool(connection.get("is_pfsense")),
    }


def _access_journey(result: dict[str, Any]) -> list[dict[str, Any]]:
    connection = dict(result.get("connection") or {})
    recorded = connection.get("access_journey")
    if isinstance(recorded, list) and recorded:
        return [dict(item) for item in recorded if isinstance(item, dict)]
    if connection.get("mode") != "vpn_menu":
        return [
            {
                "step": "direct_ssh",
                "label": "Conexão SSH direta",
                "status": "completed" if connection else "not_recorded",
                "detail": "Sessão autenticada diretamente no alvo." if connection else "Fluxo de acesso não registrado.",
            }
        ]

    client = str(connection.get("client_name") or connection.get("vpn_ip") or "cliente")
    rows = [
        ("bastion", "Monitor 1", "Conexão com o servidor de VPN autenticada."),
        ("inventory", "Inventário VPN", f"Cliente localizado no inventário: {client}."),
        ("selection", "Seleção da linha", f"Linha {connection.get('vpn_index')} selecionada para o IP {connection.get('vpn_ip')}."),
        ("confirmation", "Confirmação de acesso", "Confirmação y enviada ao menu VPN."),
        ("authentication", "Autenticação no destino", f"Sessão autenticada como {connection.get('username') or 'usuário configurado'}."),
    ]
    if connection.get("is_pfsense"):
        rows.append(("pfsense_shell", "Shell do pfSense", "Opção 8 selecionada e shell administrativo aberto."))
    rows.append(("target_shell", "Shell do alvo", "Shell remoto validado e pronto para coleta."))
    return [
        {"step": step, "label": label, "status": "completed", "detail": detail}
        for step, label, detail in rows
    ]


def _hypothesis_state(result: dict[str, Any]) -> dict[str, list[str]]:
    plans = [item for item in result.get("plans") or [] if isinstance(item, dict)]
    assessments = [item for item in result.get("round_assessments") or [] if isinstance(item, dict)]
    analysis = dict(result.get("analysis") or {})

    hypotheses = _flatten_text(
        analysis.get("hypotheses"),
        *(item.get("hypotheses") for item in plans),
    )
    confirmed = _flatten_text(
        analysis.get("confirmed_hypotheses"),
        *(item.get("hypotheses_confirmed") for item in assessments),
    )
    discarded = _flatten_text(
        analysis.get("discarded_hypotheses"),
        *(item.get("hypotheses_discarded") for item in assessments),
    )
    missing = _flatten_text(
        analysis.get("missing_information"),
        *(item.get("remaining_questions") for item in assessments),
        (analysis.get("critic") or {}).get("missing_evidence") if isinstance(analysis.get("critic"), dict) else None,
    )
    unavailable = (result.get("tool_feedback") or {}).get("unavailable") if isinstance(result.get("tool_feedback"), dict) else []
    missing.extend(f"Ferramenta indisponível: {item}." for item in unavailable or [])

    confirmed_keys = {item.casefold() for item in confirmed}
    discarded_keys = {item.casefold() for item in discarded}
    active = [
        item for item in hypotheses
        if item.casefold() not in confirmed_keys and item.casefold() not in discarded_keys
    ]
    return {
        "active": _unique_text(active),
        "confirmed": confirmed,
        "discarded": discarded,
        "missing": _unique_text(missing),
    }


def _recurrence(result: dict[str, Any]) -> dict[str, Any]:
    direct = [item for item in result.get("history") or [] if isinstance(item, dict)]
    similar = [item for item in result.get("similar_history") or [] if isinstance(item, dict)]
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*direct, *similar]:
        key = str(item.get("id") or json.dumps(item, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        cases.append(item)

    causes = _unique_text(
        _analysis_from_case(item).get("probable_cause") or item.get("probable_cause") or item.get("root_cause")
        for item in cases
    )
    normalized = [re.sub(r"\s+", " ", cause.casefold()).strip() for cause in causes]
    most_common = Counter(normalized).most_common(1)
    repeated_cause = None
    if most_common and most_common[0][1] >= 2:
        key = most_common[0][0]
        repeated_cause = next((cause for cause in causes if cause.casefold() == key), None)

    total = len(cases)
    same_target_count = len(direct)
    recurring = total >= 2
    summary = (
        f"Foram encontradas {total} ocorrência(s) relacionada(s), sendo {same_target_count} no mesmo alvo."
        if total
        else "Nenhuma ocorrência anterior relacionada foi encontrada."
    )
    if repeated_cause:
        summary += f" A causa recorrente mais frequente foi: {repeated_cause}"
    return {
        "recurring": recurring,
        "total": total,
        "same_target_count": same_target_count,
        "similar_count": max(0, total - same_target_count),
        "last_occurrence": _latest_date(cases),
        "previous_probable_causes": causes[:5],
        "repeated_cause": repeated_cause,
        "summary": summary,
    }


def _playbook_match(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("playbook")
    if not isinstance(summary, dict) or not summary.get("id"):
        return {
            "selected": False,
            "score": 0,
            "title": None,
            "id": None,
            "reasons": ["Nenhum playbook inicial foi selecionado; a análise seguiu somente pelo planejador adaptativo."],
        }

    objective = str(result.get("context") or "")
    profile = str(result.get("profile") or "unknown")
    reasons: list[str] = []
    score = 0
    matched_patterns: list[str] = []
    try:
        playbook = get_playbook(str(summary["id"]))
        static_score = playbook.score(objective, profile)
        score = _clamp(static_score)
        if profile in playbook.profiles or "any" in playbook.profiles:
            reasons.append(f"Perfil compatível: {profile}.")
        for pattern in playbook.patterns:
            try:
                matched = bool(re.search(pattern, objective, flags=re.IGNORECASE))
            except re.error:
                matched = pattern.casefold() in objective.casefold()
            if matched:
                matched_patterns.append(pattern)
        if matched_patterns:
            reasons.append(f"{len(matched_patterns)} padrão(ões) do alerta corresponderam ao playbook.")
        learning = summary.get("database_learning")
        if isinstance(learning, dict) and learning:
            successes = int(learning.get("successful_cases") or learning.get("successes") or 0)
            if successes:
                reasons.append(f"Memória operacional: {successes} caso(s) anterior(es) útil(eis) com este playbook.")
    except LookupError:
        reasons.append("O playbook selecionado não está mais disponível no catálogo atual.")

    if not reasons:
        reasons.append("Playbook selecionado pela política atual e pela memória operacional.")
    return {
        "selected": True,
        "score": score,
        "title": summary.get("title") or summary.get("id"),
        "id": summary.get("id"),
        "matched_patterns": matched_patterns,
        "reasons": reasons,
    }


def _execution_controls(result: dict[str, Any], settings: Settings) -> dict[str, Any]:
    planned: list[str] = []
    for plan in result.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        for item in plan.get("tools") or plan.get("commands") or []:
            if not isinstance(item, dict):
                continue
            planned.append(
                json.dumps(
                    {
                        "tool": item.get("tool"),
                        "arguments": item.get("arguments") or {},
                        "command": item.get("command"),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
    unique_planned = set(planned)
    evidence = [item for item in result.get("evidence") or [] if isinstance(item, dict)]
    timeout_count = 0
    failed_count = 0
    for item in evidence:
        text = f"{item.get('reason', '')} {item.get('stderr', '')}".casefold()
        if "timeout" in text or "excedeu o timeout" in text:
            timeout_count += 1
        if str(item.get("status") or "") in {"failed", "blocked", "unavailable"}:
            failed_count += 1
    return {
        "adaptive_rounds": len(result.get("round_assessments") or []),
        "planned_requests": len(planned),
        "unique_planned_requests": len(unique_planned),
        "duplicate_requests_ignored": max(0, len(planned) - len(unique_planned)),
        "evidence_collected": len(evidence),
        "failed_or_unavailable": failed_count,
        "timeouts": timeout_count,
        "max_rounds": settings.agent_max_rounds,
        "max_commands": settings.agent_max_commands,
        "command_limit_reached": len(evidence) >= settings.agent_max_commands,
    }


def _quality(result: dict[str, Any], target: dict[str, Any], hypotheses: dict[str, list[str]]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    evidence = [item for item in result.get("evidence") or [] if isinstance(item, dict)]
    successful = sum(1 for item in evidence if str(item.get("status") or "") == "executed" or item.get("exit_code") == 0)
    identification_parts = [target.get("vpn_ip"), target.get("environment"), target.get("profile"), target.get("client_name") or target.get("hostname")]
    identification = _clamp(sum(1 for item in identification_parts if item) / len(identification_parts) * 100)
    access = _access_journey(result)
    connectivity = 100 if access and access[-1].get("status") == "completed" else 30 if access else 0
    coverage = _clamp(min(1.0, len(evidence) / 6) * 70 + min(1.0, successful / 4) * 30)
    confidence = _clamp(analysis.get("confidence"))
    diagnostic = _clamp(confidence * 0.65 + min(100, len(analysis.get("facts") or []) * 15) * 0.2 + min(100, len(analysis.get("evidence_map") or []) * 20) * 0.15)
    validation = 100 if analysis.get("status") == "healthy" and analysis.get("evidence_map") else 70 if analysis.get("recommendations") else 35
    if hypotheses["missing"]:
        validation = max(0, validation - min(30, len(hypotheses["missing"]) * 8))
    overall = _clamp(identification * 0.15 + connectivity * 0.15 + coverage * 0.25 + diagnostic * 0.3 + validation * 0.15)
    return {
        "identification": identification,
        "connectivity": connectivity,
        "evidence_coverage": coverage,
        "diagnostic": diagnostic,
        "final_validation": validation,
        "overall": overall,
    }


def enrich_investigation_result(
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Adiciona explicabilidade determinística sem inventar fatos ou causas."""
    settings = settings or get_settings()
    analysis = dict(result.get("analysis") or {})
    target = _target_context(result)
    access = _access_journey(result)
    hypotheses = _hypothesis_state(result)
    recurrence = _recurrence(result)
    playbook = _playbook_match(result)
    controls = _execution_controls(result, settings)
    quality = _quality(result, target, hypotheses)

    facts = _unique_text(analysis.get("facts") or [])
    if target.get("client_name"):
        facts.insert(0, f"Cliente identificado no inventário VPN: {target['client_name']}.")
    if access and access[-1].get("status") == "completed":
        facts.append("O caminho de acesso até o shell do alvo foi concluído e validado.")
    analysis["facts"] = _unique_text(facts)
    analysis["hypotheses"] = hypotheses["active"]
    analysis["confirmed_hypotheses"] = hypotheses["confirmed"]
    analysis["discarded_hypotheses"] = hypotheses["discarded"]
    analysis["missing_information"] = hypotheses["missing"]
    analysis["target_context"] = target
    analysis["access_journey"] = access
    analysis["recurrence"] = recurrence
    analysis["playbook_match"] = playbook
    analysis["execution_controls"] = controls
    analysis["quality"] = quality

    recommendations = _unique_text(analysis.get("recommendations") or [])
    if recommendations:
        next_step = recommendations[0]
    elif hypotheses["missing"]:
        next_step = f"Coletar a evidência pendente: {hypotheses['missing'][0]}"
    elif analysis.get("status") == "healthy":
        next_step = "Manter o acompanhamento pelo monitoramento; nenhuma alteração é necessária neste momento."
    else:
        next_step = "Reexecutar a investigação caso o sintoma permaneça, preservando as evidências atuais para comparação."
    analysis["next_safe_step"] = next_step
    analysis["explainability"] = {
        "where_stopped": "Investigação concluída no shell do alvo." if access and access[-1].get("status") == "completed" else "O ponto de parada não foi persistido no resultado final.",
        "what_is_proven": analysis["facts"],
        "what_is_hypothesis": hypotheses["active"],
        "most_probable_cause": analysis.get("probable_cause") or "Nenhuma causa provável sustentada foi definida.",
        "next_safe_step": next_step,
    }
    result["analysis"] = analysis
    result["display_target"] = target.get("client_name") or target.get("hostname") or result.get("target")
    return result
