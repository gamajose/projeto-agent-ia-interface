from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.settings import Settings, get_settings
from app.services.intelligent_agent import resilient_model_call
from app.services.redaction import redact_object
from app.services.secrets import get_secret


_COMMUNICATION_PROMPT = """
Você é o agente de comunicação de um NOC de infraestrutura. Responda somente JSON válido.
Use apenas os fatos recebidos. Não invente contato, prazo, causa, ação executada ou normalização.
Escreva em português brasileiro, profissional, direto e natural.

Produza:
- ticket: atualização técnica para o chamado, clara para cliente e equipe técnica;
- whatsapp: mensagem curta para o cliente;
- internal: resumo operacional para NOC/Infra/N3;
- escalation: texto objetivo para transferência, com Descrição do Problema, Ações já realizadas e Motivo da Transferência;
- risk_letter: somente quando houver risco relevante/ausência de monitoramento/necessidade de intervenção; caso contrário string vazia.

Formato:
{
  "ticket":"...",
  "whatsapp":"...",
  "internal":"...",
  "escalation":"...",
  "risk_letter":"..."
}
""".strip()


def escalation_team(incident: dict[str, Any], analysis: dict[str, Any]) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            incident.get("service"),
            incident.get("last_output"),
            analysis.get("probable_cause"),
            analysis.get("conclusion"),
        )
    ).casefold()
    if any(token in text for token in ("inode", "filesystem", "disco", "disk", "raid", "i/o error", "corrupt")):
        return "infra_storage"
    if any(token in text for token in ("idrac", "ilo", "ilom", "xclarity", "ipmi", "hardware", "sensor", "fonte", "power supply")):
        return "infra_hardware"
    if any(token in text for token in ("snmp", "udp/161", "porta 161", "authorizationerror")):
        return "infra_network_hardware"
    if any(token in text for token in ("vpn", "openvpn", "ipsec", "gateway", "dpinger", "rota", "route", "flapping")):
        return "network"
    if any(token in text for token in ("checkmk", "omd", "rrdcached", "automation-helper", "6556", "xinetd", "agent")):
        return "noc_monitoring"
    if any(token in text for token in ("memory", "memória", "swap", "segfault", "kernel")):
        return "infra_os"
    return "infra_n3"


def _fallback(incident: dict[str, Any], analysis: dict[str, Any], *, state: str) -> dict[str, str]:
    host = str(incident.get("host") or "host não informado")
    service = str(incident.get("service") or "serviço não informado")
    cause = str(analysis.get("probable_cause") or incident.get("probable_cause") or "causa ainda não confirmada")
    conclusion = str(analysis.get("conclusion") or incident.get("conclusion") or "")
    status = str(state or incident.get("status") or "em análise")
    monitoring_risk = any(
        token in f"{service} {cause} {conclusion}".casefold()
        for token in ("checkmk", "monitor", "6556", "omd", "snmp")
    )
    ticket = (
        f"Identificamos o alerta no host {host}, serviço {service}. "
        f"O incidente está com status {status}. Causa provável: {cause}. "
        + (f"{conclusion}" if conclusion else "A análise automática segue registrada com as evidências coletadas.")
    )
    whatsapp = (
        f"Olá! Identificamos uma indisponibilidade relacionada ao monitoramento do host {host} ({service}). "
        f"A análise apontou: {cause}. "
        + ("O ambiente segue em acompanhamento pela nossa automação." if status != "resolved" else "A normalização já foi confirmada pelo monitoramento.")
    )
    internal = f"[{status}] {host} / {service} — {cause}. {conclusion}".strip()
    escalation = (
        f"Descrição do Problema: alerta {service} no host {host}; status atual {status}.\n\n"
        f"Ações já realizadas: investigação automática e coleta de evidências pelo Agent IA. Causa provável: {cause}.\n\n"
        f"Motivo da Transferência: {conclusion or 'o incidente requer intervenção fora do envelope de correção automática do NOC.'}"
    )
    risk_letter = ""
    if monitoring_risk and status in {"needs_attention", "critical", "inconclusive"}:
        risk_letter = (
            f"O host {host} permanece com comprometimento do monitoramento relacionado ao serviço {service}. "
            "Enquanto a condição persistir, eventos e indisponibilidades do ambiente podem deixar de ser detectados ou comunicados no tempo esperado. "
            f"A causa provável identificada até o momento é: {cause}. É necessária atuação para restabelecer a cobertura de monitoramento."
        )
    return {
        "ticket": ticket.strip(),
        "whatsapp": whatsapp.strip(),
        "internal": internal.strip(),
        "escalation": escalation.strip(),
        "risk_letter": risk_letter.strip(),
    }


def build_incident_communications(
    incident: dict[str, Any],
    result: dict[str, Any] | None = None,
    *,
    state: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    result = dict(result or {})
    analysis = dict(result.get("analysis") or {}) or {
        "probable_cause": incident.get("probable_cause"),
        "conclusion": incident.get("conclusion"),
        "status": incident.get("analysis_status"),
    }
    fallback = _fallback(incident, analysis, state=state or str(incident.get("status") or ""))
    diagnostics: dict[str, Any] = {"success": False, "source": "deterministic_fallback"}
    messages = fallback

    if settings.noc_communication_ai_enabled:
        payload = redact_object(
            {
                "incident": {
                    "host": incident.get("host"),
                    "service": incident.get("service"),
                    "site": incident.get("site"),
                    "environment": incident.get("environment"),
                    "status": state or incident.get("status"),
                    "severity": incident.get("severity"),
                    "flapping": incident.get("flapping"),
                    "recent_transition_count": incident.get("recent_transition_count"),
                    "last_output": incident.get("last_output"),
                },
                "analysis": {
                    "status": analysis.get("status"),
                    "confidence": analysis.get("confidence"),
                    "facts": list(analysis.get("facts") or [])[:12],
                    "probable_cause": analysis.get("probable_cause"),
                    "conclusion": analysis.get("conclusion"),
                    "recommendations": list(analysis.get("recommendations") or [])[:8],
                    "ticket_report": analysis.get("ticket_report"),
                    "correction_status": analysis.get("correction_status"),
                    "correction_validation": analysis.get("correction_validation"),
                },
            }
        )
        generated, diagnostics = resilient_model_call(
            _COMMUNICATION_PROMPT + "\n\nDADOS:\n" + json.dumps(payload, ensure_ascii=False, default=str),
            "noc_communication",
        )
        if isinstance(generated, dict):
            candidate = {
                key: str(generated.get(key) or fallback[key]).strip()
                for key in fallback
            }
            if candidate["ticket"] and candidate["whatsapp"] and candidate["internal"]:
                messages = candidate

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "team": escalation_team(incident, analysis),
        "messages": messages,
        "ai_diagnostics": diagnostics,
    }


def _post_webhook(url: str, payload: dict[str, Any], *, token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.post(url, json=redact_object(payload), headers=headers, timeout=20)
        response.raise_for_status()
        return {"status": "published", "status_code": response.status_code}
    except Exception as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def publish_incident_communications(
    incident: dict[str, Any],
    communications: dict[str, Any],
    *,
    phase: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    messages = dict(communications.get("messages") or {})
    base_payload = {
        "source": "agent-ia-noc",
        "phase": phase,
        "incident_id": incident.get("id"),
        "host": incident.get("host"),
        "service": incident.get("service"),
        "site": incident.get("site"),
        "environment": incident.get("environment"),
        "status": incident.get("status"),
        "severity": incident.get("severity"),
        "flapping": bool(incident.get("flapping")),
        "team": communications.get("team"),
        "messages": messages,
    }
    delivery: dict[str, Any] = {
        "helpdesk": {"status": "disabled"},
        "whatsapp": {"status": "disabled"},
        "internal": {"status": "disabled"},
    }

    if settings.helpdesk_publish_automatically and settings.helpdesk_webhook_url:
        token = get_secret("HELPDESK_WEBHOOK_TOKEN", settings.helpdesk_webhook_token, settings=settings)
        delivery["helpdesk"] = _post_webhook(
            settings.helpdesk_webhook_url,
            {**base_payload, "message": messages.get("ticket"), "escalation": messages.get("escalation")},
            token=token,
        )

    if settings.noc_whatsapp_auto_send and settings.noc_whatsapp_webhook_url:
        token = get_secret("NOC_WHATSAPP_WEBHOOK_TOKEN", settings.noc_whatsapp_webhook_token, settings=settings)
        delivery["whatsapp"] = _post_webhook(
            settings.noc_whatsapp_webhook_url,
            {**base_payload, "message": messages.get("whatsapp")},
            token=token,
        )

    if settings.noc_internal_webhook_url:
        token = get_secret("NOC_INTERNAL_WEBHOOK_TOKEN", settings.noc_internal_webhook_token, settings=settings)
        delivery["internal"] = _post_webhook(
            settings.noc_internal_webhook_url,
            {**base_payload, "message": messages.get("internal")},
            token=token,
        )

    return {
        "phase": phase,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "delivery": delivery,
    }
