from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.models import InvestigationORM
from app.services.ai_providers import get_provider
from app.services.provider_router import resolve_automatic_provider
from app.services.redaction import redact_object


_USER_FIELDS = (
    "summary",
    "facts",
    "hypotheses",
    "confirmed_hypotheses",
    "discarded_hypotheses",
    "missing_information",
    "probable_cause",
    "conclusion",
    "recommendations",
    "next_safe_step",
    "evidence_map",
)
_ENGLISH_WORDS = {
    "the", "and", "this", "that", "with", "from", "server", "memory", "service",
    "evidence", "summary", "likely", "cause", "recommendation", "should", "investigation",
    "confidence", "status", "running", "stopped", "available", "usage", "issue", "because",
}
_PORTUGUESE_WORDS = {
    "o", "a", "os", "as", "e", "com", "sem", "servidor", "memória", "memoria",
    "serviço", "servico", "evidência", "evidencia", "resumo", "causa", "recomendação",
    "recomendacao", "deve", "investigação", "investigacao", "confiança", "confianca",
    "estado", "execução", "execucao", "disponível", "disponivel", "utilização", "utilizacao",
}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        rows: list[str] = []
        for item in value.values():
            rows.extend(_strings(item))
        return rows
    if isinstance(value, (list, tuple)):
        rows = []
        for item in value:
            rows.extend(_strings(item))
        return rows
    return []


def _needs_ptbr(value: Any) -> bool:
    text = " ".join(_strings(value)).casefold()
    words = re.findall(r"[a-záàâãéêíóôõúç]+", text)
    english = sum(1 for word in words if word in _ENGLISH_WORDS)
    portuguese = sum(1 for word in words if word in _PORTUGUESE_WORDS)
    return english >= 4 and english > portuguese


def _localization_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    payload = {key: analysis.get(key) for key in _USER_FIELDS if key in analysis}
    review = dict(analysis.get("review") or {})
    if review:
        payload["review"] = {
            key: review.get(key)
            for key in ("reason", "risks", "action_reviews")
            if key in review
        }
    critic = dict(analysis.get("critic") or {})
    if critic:
        payload["critic"] = {
            key: critic.get(key)
            for key in (
                "summary",
                "supported_claims",
                "unsupported_claims",
                "contradictions",
                "missing_evidence",
            )
            if key in critic
        }
    recurrence = dict(analysis.get("recurrence") or {})
    if recurrence:
        payload["recurrence"] = {
            "summary": recurrence.get("summary"),
            "previous_probable_causes": recurrence.get("previous_probable_causes"),
            "repeated_cause": recurrence.get("repeated_cause"),
        }
    playbook = dict(analysis.get("playbook_match") or {})
    if playbook:
        payload["playbook_match"] = {"reasons": playbook.get("reasons")}
    explainability = dict(analysis.get("explainability") or {})
    if explainability:
        payload["explainability"] = explainability
    access = analysis.get("access_journey")
    if isinstance(access, list):
        payload["access_journey"] = [
            {"label": item.get("label"), "detail": item.get("detail")}
            for item in access
            if isinstance(item, dict)
        ]
    return payload


def _translation_provider(
    result: dict[str, Any],
    settings: Settings,
) -> tuple[str, str | None]:
    provider_name = str(
        result.get("selected_provider")
        or (result.get("provider_selection") or {}).get("provider")
        or settings.ai_provider
        or "gemini"
    ).strip().lower()
    model = str(
        result.get("selected_model")
        or (result.get("provider_selection") or {}).get("model")
        or ""
    ).strip() or None
    if provider_name == "auto":
        selection = resolve_automatic_provider(settings)
        return selection.provider, selection.model or model
    return provider_name, model


def _merge_nested_translation(
    merged: dict[str, Any],
    translated: dict[str, Any],
    key: str,
) -> None:
    if isinstance(translated.get(key), dict):
        merged[key] = {**dict(merged.get(key) or {}), **translated[key]}
    elif isinstance(translated.get(key), list):
        merged[key] = translated[key]


def _translate_user_fields(
    analysis: dict[str, Any],
    result: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    payload = _localization_payload(analysis)
    if not payload or not _needs_ptbr(payload):
        return analysis

    try:
        provider_name, model = _translation_provider(result, settings)
        prompt = (
            "Você é um revisor de idioma técnico. Responda somente JSON válido.\n"
            "Traduza TODOS os valores textuais para português do Brasil natural e profissional.\n"
            "Preserve exatamente as chaves, números, percentuais, nomes de ferramentas, comandos, IPs, "
            "hostnames, modelos, estados técnicos e a estrutura de listas/objetos.\n"
            "Não acrescente conclusões, não altere confiança e não invente evidências.\n\n"
            "CONTEÚDO:\n"
            + json.dumps(redact_object(payload), ensure_ascii=False, default=str)
        )
        provider = get_provider(provider_name, settings, model)
        translated, _metadata = provider.generate_json(prompt)
        if not isinstance(translated, dict):
            return analysis
        merged = dict(analysis)
        for key in _USER_FIELDS:
            if key in translated:
                merged[key] = translated[key]
        for key in (
            "review",
            "critic",
            "recurrence",
            "playbook_match",
            "explainability",
        ):
            _merge_nested_translation(merged, translated, key)
        if isinstance(translated.get("access_journey"), list):
            original = [dict(item) for item in merged.get("access_journey") or [] if isinstance(item, dict)]
            for index, translated_item in enumerate(translated["access_journey"]):
                if index < len(original) and isinstance(translated_item, dict):
                    original[index].update(translated_item)
            merged["access_journey"] = original
        return merged
    except Exception:
        return analysis


def _status_label(status: str) -> str:
    return {
        "healthy": "Saudável",
        "attention": "Atenção",
        "critical": "Crítico",
        "inconclusive": "Inconclusivo",
    }.get(status, status or "Inconclusivo")


def _clean_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def build_ticket_report_ptbr(analysis: dict[str, Any]) -> str:
    confidence = max(0, min(100, int(analysis.get("confidence") or 0)))
    status = str(analysis.get("status") or "inconclusive")
    facts = _clean_list(analysis.get("facts"))
    hypotheses = _clean_list(analysis.get("hypotheses"))
    discarded = _clean_list(analysis.get("discarded_hypotheses"))
    missing = _clean_list(analysis.get("missing_information"))
    recommendations = _clean_list(analysis.get("recommendations"))
    target = dict(analysis.get("target_context") or {})
    recurrence = dict(analysis.get("recurrence") or {})
    playbook = dict(analysis.get("playbook_match") or {})
    quality = dict(analysis.get("quality") or {})
    access = [item for item in analysis.get("access_journey") or [] if isinstance(item, dict)]
    incident = dict(analysis.get("incident_intelligence") or {})
    correlation = dict(incident.get("alert_correlation") or {})
    conclusion_validation = dict(incident.get("conclusion_validation") or {})
    freshness = dict(incident.get("evidence_freshness") or {})
    dependency_map = dict(incident.get("dependency_map") or {})
    correction_validation = dict(analysis.get("correction_validation") or {})

    display_target = target.get("client_name") or target.get("hostname") or target.get("vpn_ip")
    rows = [
        f"Alvo: {display_target or 'não identificado'}",
        f"IP: {target.get('vpn_ip') or 'não informado'}",
        f"Ambiente: {target.get('environment') or 'unknown'}",
        f"Status da análise: {_status_label(status)}",
        f"Confiança validada: {confidence}%",
    ]
    if quality:
        rows.append(f"Qualidade geral da investigação: {int(quality.get('overall') or 0)}%")
    rows.extend(
        [
            "",
            "Resumo operacional:",
            str(analysis.get("summary") or "A investigação foi concluída sem resumo textual.").strip(),
        ]
    )

    if access:
        rows.extend(["", "Caminho de acesso:"])
        for item in access:
            marker = "OK" if item.get("status") == "completed" else str(item.get("status") or "pendente").upper()
            rows.append(f"- [{marker}] {item.get('label')}: {item.get('detail')}")

    if correlation:
        primary = dict(correlation.get("primary_alert") or {})
        rows.extend(["", "Correlação de alertas:"])
        rows.append(f"- Alerta primário: {primary.get('label') or 'não classificado'}")
        for item in correlation.get("related_alerts") or []:
            if isinstance(item, dict):
                rows.append(f"- Alerta relacionado: {item.get('label') or item.get('objective')}")
        rows.append(f"- Avaliação: {correlation.get('reason')}")

    probable_cause = str(analysis.get("probable_cause") or "").strip()
    if probable_cause:
        rows.extend(["", "Causa provável:", probable_cause])
    conclusion = str(analysis.get("conclusion") or "").strip()
    if conclusion:
        rows.extend(["", "Conclusão:", conclusion])

    if conclusion_validation:
        rows.extend(["", "Validação independente da conclusão:"])
        rows.append(f"- Veredito: {conclusion_validation.get('verdict') or 'não disponível'}")
        for item in conclusion_validation.get("contradictions") or []:
            rows.append(f"- Contradição: {item}")
        rows.append(f"- Recomendação: {conclusion_validation.get('recommendation')}")

    if dependency_map.get("nodes"):
        chain = " -> ".join(str(item.get("label")) for item in dependency_map["nodes"] if isinstance(item, dict))
        rows.extend(["", "Mapa de dependências:", f"- {chain}"])
        if dependency_map.get("missing_layers"):
            rows.append(f"- Camadas não identificadas: {', '.join(dependency_map['missing_layers'])}")

    if freshness:
        rows.extend(["", "Validade das evidências:", f"- {freshness.get('summary')}"])
        rows.append(f"- Cobertura de horário: {freshness.get('timestamp_coverage', 0)}%")

    if facts:
        rows.extend(["", "Fatos comprovados:", *[f"- {item}" for item in facts[:12]]])
    if hypotheses:
        rows.extend(["", "Hipóteses ainda em avaliação:", *[f"- {item}" for item in hypotheses[:8]]])
    if discarded:
        rows.extend(["", "Hipóteses descartadas:", *[f"- {item}" for item in discarded[:8]]])
    if missing:
        rows.extend(["", "Evidências ainda necessárias:", *[f"- {item}" for item in missing[:8]]])

    if playbook.get("selected"):
        rows.extend(
            [
                "",
                "Playbook selecionado:",
                f"- {playbook.get('title') or playbook.get('id')} · compatibilidade {int(playbook.get('score') or 0)}%",
            ]
        )
        rows.extend(f"- {item}" for item in _clean_list(playbook.get("reasons"))[:5])

    if recurrence.get("total"):
        rows.extend(["", "Recorrência:", f"- {recurrence.get('summary')}"])
    if correction_validation:
        rows.extend(["", "Comparação antes e depois:", f"- {correction_validation.get('summary')}"])
        rows.append(f"- Status da pós-validação: {correction_validation.get('status')}")
    if recommendations:
        rows.extend(["", "Recomendações:", *[f"- {item}" for item in recommendations[:12]]])

    next_step = str(analysis.get("next_safe_step") or "").strip()
    if next_step:
        rows.extend(["", "Próximo passo mais seguro:", next_step])
    return "\n".join(rows).strip()


def _sync_investigation(result: dict[str, Any], analysis: dict[str, Any]) -> None:
    investigation_id = result.get("investigation_id") or result.get("id")
    if not investigation_id:
        return
    try:
        identifier = uuid.UUID(str(investigation_id))
    except ValueError:
        return
    try:
        ensure_database_schema()
        with SessionLocal() as session:
            row = session.get(InvestigationORM, identifier)
            if not row:
                return
            row.analysis = redact_object(analysis)
            row.status = str(analysis.get("status") or row.status or "inconclusive")
            row.confidence = max(0, min(100, int(analysis.get("confidence") or 0)))
            if isinstance(result.get("evidence"), list):
                row.evidence = redact_object(result["evidence"])
            if isinstance(result.get("plans"), list):
                row.plans = redact_object(result["plans"])
            if isinstance(result.get("round_assessments"), list):
                row.assessments = redact_object(result["round_assessments"])
            if isinstance(result.get("ai_diagnostics"), list):
                row.diagnostics = redact_object(result["ai_diagnostics"])
            session.commit()
    except Exception:
        return


def finalize_result_presentation(
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Garante pt-BR, explicabilidade, inteligência do incidente e uma única confiança."""
    settings = settings or get_settings()
    analysis = dict(result.get("analysis") or {})
    analysis = _translate_user_fields(analysis, result, settings)
    analysis["confidence"] = max(0, min(100, int(analysis.get("confidence") or 0)))
    analysis["language"] = "pt-BR"
    analysis["ticket_report"] = build_ticket_report_ptbr(analysis)
    result["analysis"] = analysis
    result["status"] = analysis.get("status")
    result["confidence"] = analysis.get("confidence")
    _sync_investigation(result, analysis)
    return result
