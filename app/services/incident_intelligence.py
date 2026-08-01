from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable


_ACCESS_FAILURES = {
    "bastion": {
        "code": "monitor1_unavailable",
        "layer": "Monitor 1",
        "summary": "Não foi possível autenticar ou abrir o terminal do Monitor 1.",
        "next_step": "Validar comunicação, serviço SSH e credencial do Monitor 1 antes de investigar o cliente.",
    },
    "inventory": {
        "code": "vpn_inventory_unavailable",
        "layer": "Inventário VPN",
        "summary": "O comando VPN não retornou um inventário utilizável dentro do prazo.",
        "next_step": "Executar o comando vpn manualmente no Monitor 1 e validar se o inventário e o prompt de seleção são exibidos.",
    },
    "selection": {
        "code": "vpn_target_not_selected",
        "layer": "Seleção do cliente",
        "summary": "O alvo não pôde ser selecionado no inventário VPN.",
        "next_step": "Confirmar se o IP está cadastrado no inventário e se a linha correspondente permanece ativa.",
    },
    "confirmation": {
        "code": "vpn_confirmation_failed",
        "layer": "Confirmação VPN",
        "summary": "O menu VPN não aceitou ou não concluiu a confirmação de acesso.",
        "next_step": "Validar manualmente a resposta do menu após selecionar a linha e confirmar com y.",
    },
    "authentication": {
        "code": "target_authentication_failed",
        "layer": "Autenticação do destino",
        "summary": "O caminho VPN chegou ao destino, mas a autenticação SSH não foi concluída.",
        "next_step": "Validar bloqueio, expiração ou alteração da credencial do usuário apresentado pelo destino; não reiniciar a VPN.",
    },
    "pfsense_shell": {
        "code": "pfsense_shell_unavailable",
        "layer": "Menu do pfSense",
        "summary": "A autenticação ocorreu, mas o menu do pfSense ou a opção de shell não foi disponibilizada.",
        "next_step": "Validar o perfil do usuário root e se a opção 8 continua disponível no console do pfSense.",
    },
    "target_shell": {
        "code": "target_shell_unavailable",
        "layer": "Shell do destino",
        "summary": "A sessão foi iniciada, mas o shell remoto não respondeu ao teste de prontidão.",
        "next_step": "Validar banner, shell padrão, restrições de terminal e encerramento prematuro da sessão no destino.",
    },
    "sudo": {
        "code": "sudo_unavailable",
        "layer": "Elevação sudo",
        "summary": "O acesso ao alvo funciona, porém a elevação sudo necessária para a coleta foi recusada.",
        "next_step": "Validar permissão sudo do usuário sem alterar a conectividade VPN ou reiniciar o servidor.",
    },
    "command": {
        "code": "command_timeout",
        "layer": "Coleta técnica",
        "summary": "O acesso ao alvo funciona, mas um comando de coleta excedeu o tempo permitido.",
        "next_step": "Revisar o comando, reduzir o escopo da coleta e validar possível bloqueio no processo consultado.",
    },
}

_ALERT_RULES = (
    ("automation_helper", 100, re.compile(r"automation[- ]?helper|automation helpers?|process(?:o)?\s+[a-z0-9_-]+\s+automation", re.I)),
    ("omd_status", 80, re.compile(r"\bomd\b.*\bstatus\b|site\s+omd|partially running|parcialmente iniciado", re.I)),
    ("container_health", 60, re.compile(r"docker container health|container\s+unhealthy|healthcheck", re.I)),
    ("checkmk_agent", 55, re.compile(r"porta\s+6556|check[_ -]?mk agent|agente checkmk", re.I)),
    ("snmp", 50, re.compile(r"\bsnmp\b|udp\s*161|authorizationerror", re.I)),
    ("vpn", 45, re.compile(r"\bvpn\b|flapping|tap\d+|t[uú]nel", re.I)),
    ("filesystem", 40, re.compile(r"filesystem|disco|inode|no space left", re.I)),
    ("memory", 35, re.compile(r"mem[oó]ria|swap|oom", re.I)),
)

_ALERT_LABELS = {
    "automation_helper": "Processo automation-helper",
    "omd_status": "Estado do site OMD",
    "container_health": "Saúde do container",
    "checkmk_agent": "Agente Checkmk / porta 6556",
    "snmp": "Comunicação SNMP",
    "vpn": "Conectividade VPN",
    "filesystem": "Filesystem / inodes",
    "memory": "Memória / swap",
    "generic": "Alerta operacional",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[Any]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        rows.append(text)
    return rows


def _failed_access_step(journey: list[dict[str, Any]]) -> str | None:
    for item in reversed(journey):
        if _text(item.get("status")).casefold() == "failed":
            return _text(item.get("step")) or None
    return None


def classify_access_failure(
    error: BaseException | str | None,
    journey: list[dict[str, Any]] | None = None,
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    rows = [dict(item) for item in journey or [] if isinstance(item, dict)]
    raw = _text(error)
    normalized = raw.casefold()
    selected_stage = stage or _failed_access_step(rows)

    if "sudo" in normalized and any(token in normalized for token in ("password", "permission", "not allowed", "necessária")):
        selected_stage = "sudo"
    elif "timeout" in normalized and any(token in normalized for token in ("comando", "command")):
        selected_stage = "command"
    elif any(token in normalized for token in ("permission denied", "authentication failed", "autenticação", "credencial")):
        selected_stage = "authentication"
    elif any(token in normalized for token in ("no route to host", "connection refused", "unable to connect", "timed out")) and not selected_stage:
        selected_stage = "bastion"

    template = _ACCESS_FAILURES.get(selected_stage or "")
    if template is None:
        return {
            "code": "access_failure_unclassified",
            "layer": "Acesso remoto",
            "stage": selected_stage,
            "summary": "A execução falhou durante o acesso remoto, mas a camada exata não pôde ser classificada.",
            "technical_detail": raw or None,
            "vpn_reached": False,
            "target_reached": False,
            "next_step": "Revisar a última etapa registrada e repetir somente a validação de acesso necessária.",
        }

    completed = {_text(item.get("step")) for item in rows if _text(item.get("status")).casefold() == "completed"}
    return {
        **template,
        "stage": selected_stage,
        "technical_detail": raw or None,
        "vpn_reached": bool(completed.intersection({"inventory", "selection", "confirmation", "authentication", "target_shell"})),
        "target_reached": bool(completed.intersection({"authentication", "pfsense_shell", "target_shell"})),
    }


def _alert_kind(text: str) -> tuple[str, int]:
    for kind, priority, pattern in _ALERT_RULES:
        if pattern.search(text):
            return kind, priority
    return "generic", 10


def _site_token(text: str) -> str | None:
    patterns = (
        r"\bomd\s+([a-z0-9_-]{2,30})\s+status\b",
        r"\bprocess(?:o)?\s+([a-z0-9_-]{2,30})\s+automation",
        r"\bsite\s+([a-z0-9_-]{2,30})\b",
        r"\bSITE=([a-z0-9_-]{2,30})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).lower()
    return None


def correlate_alerts(result: dict[str, Any]) -> dict[str, Any]:
    current_text = _text(result.get("context") or result.get("objective"))
    current_kind, current_priority = _alert_kind(current_text)
    current_site = _site_token(current_text)
    candidates: list[dict[str, Any]] = [
        {
            "source": "current",
            "kind": current_kind,
            "label": _ALERT_LABELS[current_kind],
            "priority": current_priority,
            "site": current_site,
            "objective": current_text,
        }
    ]

    for item in [*(result.get("history") or []), *(result.get("similar_history") or [])]:
        if not isinstance(item, dict):
            continue
        objective = _text(item.get("objective") or item.get("symptom"))
        if not objective:
            continue
        kind, priority = _alert_kind(objective)
        site = _site_token(objective)
        if current_site and site and site != current_site:
            continue
        candidates.append(
            {
                "source": "history",
                "investigation_id": item.get("id"),
                "kind": kind,
                "label": _ALERT_LABELS[kind],
                "priority": priority,
                "site": site,
                "objective": objective,
                "created_at": item.get("created_at"),
            }
        )

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for item in candidates:
        key = (item["kind"], item.get("site"))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)

    primary = max(deduplicated, key=lambda item: int(item.get("priority") or 0))
    related = [item for item in deduplicated if item is not primary]
    grouped = len(deduplicated) > 1 and any(
        item["kind"] in {"automation_helper", "omd_status", "container_health"}
        for item in deduplicated
    )
    reason = (
        "Os alertas descrevem camadas dependentes do mesmo site Checkmk; o processo interno é priorizado como possível causa primária."
        if grouped
        else "Não há evidência suficiente para consolidar alertas diferentes em um único incidente."
    )
    return {
        "grouped": grouped,
        "incident_key": f"{result.get('target')}:{current_site or current_kind}",
        "site": current_site,
        "primary_alert": primary,
        "related_alerts": related,
        "reason": reason,
    }


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


def validate_conclusion(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    claim = " ".join(
        filter(None, (_text(analysis.get("probable_cause")), _text(analysis.get("conclusion"))))
    ).casefold()
    observed = _observed_values(result)
    journey = [item for item in analysis.get("access_journey") or (result.get("connection") or {}).get("access_journey") or [] if isinstance(item, dict)]
    shell_completed = any(item.get("step") == "target_shell" and item.get("status") == "completed" for item in journey)
    contradictions: list[str] = []
    checks: list[dict[str, Any]] = []

    if any(token in claim for token in ("vpn indispon", "vpn caiu", "falha na vpn", "rota vpn ausente")):
        passed = not shell_completed
        checks.append({"check": "A causa atribuída à VPN é compatível com o caminho de acesso.", "passed": passed})
        if not passed:
            contradictions.append("O shell do destino foi alcançado; a VPN não pode ser tratada como causa comprovada desta execução.")

    if any(token in claim for token in ("container parado", "container caiu", "container indisponível", "container stopped")):
        container_up = bool(re.search(r"\bup\s+\d|\bup\s+about|\(unhealthy\)", observed))
        checks.append({"check": "A afirmação de container parado é compatível com o estado coletado.", "passed": not container_up})
        if container_up:
            contradictions.append("A evidência mostra o container ativo; o problema pode estar no healthcheck ou em um processo interno.")

    if any(token in claim for token in ("autenticação", "authentication", "senha incorreta", "credencial")):
        checks.append({"check": "A hipótese de autenticação é compatível com a abertura do shell.", "passed": not shell_completed})
        if shell_completed:
            contradictions.append("O shell foi validado; uma falha de autenticação não explica o incidente técnico analisado.")

    if any(token in claim for token in ("serviço parado", "service stopped", "inactive")) and re.search(r"\bactive\s*\(running\)|activestate=active|substate=running", observed):
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


def evidence_freshness(result: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    timestamped = 0
    stale = 0
    for item in result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("collected_at") or item.get("observed_at")
        age_seconds: int | None = None
        state = "timestamp_missing"
        if raw:
            try:
                collected = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if collected.tzinfo is None:
                    collected = collected.replace(tzinfo=timezone.utc)
                age_seconds = max(0, int((now - collected.astimezone(timezone.utc)).total_seconds()))
                timestamped += 1
                if age_seconds > 3600:
                    state = "stale"
                    stale += 1
                elif age_seconds > 900:
                    state = "aging"
                else:
                    state = "fresh"
            except ValueError:
                state = "timestamp_invalid"
        rows.append(
            {
                "tool": item.get("tool") or item.get("command"),
                "collected_at": raw,
                "age_seconds": age_seconds,
                "state": state,
                "exit_code": item.get("exit_code"),
                "duration_ms": item.get("duration_ms"),
            }
        )
    total = len(rows)
    return {
        "total": total,
        "timestamped": timestamped,
        "timestamp_coverage": round(timestamped / total * 100) if total else 0,
        "stale": stale,
        "items": rows,
        "summary": (
            f"{timestamped} de {total} evidência(s) possuem horário de coleta; {stale} estão com mais de uma hora."
            if total
            else "Nenhuma evidência técnica foi registrada."
        ),
    }


def build_dependency_map(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    target = dict(analysis.get("target_context") or {})
    connection = dict(result.get("connection") or {})
    inventory = dict(result.get("inventory") or {})
    objective = _text(result.get("context") or result.get("objective"))
    observed = _observed_values(result)
    site = _site_token(f"{objective}\n{observed}")

    container_match = re.search(r"(?:CONTAINER=|container\s+)([a-z0-9_.-]{2,100})", observed, flags=re.I)
    process = "automation-helper" if re.search(r"automation[- ]?helper", f"{objective}\n{observed}", flags=re.I) else None
    client = target.get("client_name") or inventory.get("client_name") or inventory.get("hostname")
    vpn_ip = target.get("vpn_ip") or connection.get("vpn_ip") or inventory.get("vpn_ip") or result.get("target")
    hostname = target.get("hostname") or inventory.get("system_hostname") or result.get("hostname")
    container = container_match.group(1) if container_match else None

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def add(node_id: str, node_type: str, label: Any) -> str | None:
        value = _text(label)
        if not value:
            return None
        nodes.append({"id": node_id, "type": node_type, "label": value})
        return node_id

    chain = [
        add("client", "client", client),
        add("vpn", "vpn_ip", vpn_ip),
        add("host", "host", hostname),
        add("container", "container", container),
        add("site", "checkmk_site", site),
        add("process", "process", process),
    ]
    previous = None
    for current in chain:
        if current and previous:
            edges.append({"from": previous, "to": current, "relation": "depends_on"})
        if current:
            previous = current

    return {
        "nodes": nodes,
        "edges": edges,
        "complete": bool(client and vpn_ip and hostname),
        "missing_layers": [
            label
            for label, value in (
                ("cliente", client),
                ("IP VPN", vpn_ip),
                ("servidor", hostname),
                ("container", container),
                ("site OMD", site),
                ("processo", process),
            )
            if not value
        ],
    }


def enrich_incident_intelligence(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    journey = [item for item in analysis.get("access_journey") or (result.get("connection") or {}).get("access_journey") or [] if isinstance(item, dict)]
    connection_failure = (result.get("connection") or {}).get("access_failure")
    if not isinstance(connection_failure, dict):
        connection_failure = None

    correlation = correlate_alerts(result)
    conclusion_validation = validate_conclusion(result)
    freshness = evidence_freshness(result)
    dependencies = build_dependency_map(result)

    confidence = max(0, min(100, int(analysis.get("confidence") or 0)))
    if conclusion_validation["verdict"] == "contradicted":
        confidence = min(confidence, 45)
        missing = list(analysis.get("missing_information") or [])
        missing.append("Reconciliar as contradições detectadas pela validação independente da conclusão.")
        analysis["missing_information"] = _unique(missing)
    elif conclusion_validation["verdict"] == "needs_more_evidence":
        confidence = min(confidence, 70)
    analysis["confidence"] = confidence

    intelligence = {
        "access_failure": connection_failure,
        "alert_correlation": correlation,
        "dependency_map": dependencies,
        "conclusion_validation": conclusion_validation,
        "evidence_freshness": freshness,
        "access_journey_complete": bool(journey and journey[-1].get("status") == "completed"),
    }
    analysis["incident_intelligence"] = intelligence
    result["incident_intelligence"] = intelligence
    result["analysis"] = analysis
    return result
