from __future__ import annotations

import json
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.ai_providers import use_provider
from app.services.evidence_timing import stamp_evidence_timing
from app.services.incident_orchestration import enrich_incident_intelligence
from app.services.intelligent_agent import run_dynamic_investigation
from app.services.investigation_budget import use_investigation_budget
from app.services.investigation_insights import enrich_investigation_result
from app.services.metrics import increment
from app.services.nested_ssh import NestedSSHExecutor
from app.services.persistence import update_investigation_analysis
from app.services.playbooks import use_playbook
from app.services.progress import report_progress
from app.services.provider_router import resolve_automatic_provider
from app.services.redaction import redact_object
from app.services.result_presentation import finalize_result_presentation
from app.services.runner import _explicit_provider_resolution, build_executor, resolve_target
from app.services.secrets import get_secret
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


_MONITOR_CORRECTION_TOOLS = {
    "checkmk.recover_omd_service",
}


def _environment(value: str | EnvironmentType | None) -> EnvironmentType:
    if isinstance(value, EnvironmentType):
        return value
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _analysis_text(analysis: dict[str, Any]) -> str:
    parts = [
        analysis.get("summary"),
        analysis.get("conclusion"),
        analysis.get("probable_cause"),
        analysis.get("root_cause"),
        analysis.get("recommendations"),
    ]
    return json.dumps(parts, ensure_ascii=False, default=str).casefold()


def _monitoring_followup_needed(result: dict[str, Any]) -> tuple[bool, str]:
    """Decide se o mesmo incidente precisa continuar no monitor do cliente.

    A troca de contexto é deliberadamente conservadora. Uma ferramenta corretiva
    que só existe no OMD/Checkmk é sinal forte. Texto da análise só dispara a
    troca quando combina contexto de monitoramento com artefatos do Checkmk/OMD.
    """

    analysis = dict(result.get("analysis") or {})
    proposed = [item for item in analysis.get("proposed_actions") or [] if isinstance(item, dict)]
    proposed_tools = {
        str(item.get("tool") or "").strip()
        for item in proposed
        if str(item.get("tool") or "").strip()
    }
    monitor_tools = proposed_tools & _MONITOR_CORRECTION_TOOLS
    if monitor_tools:
        return True, f"ação proposta pertence ao servidor de monitoramento: {', '.join(sorted(monitor_tools))}"

    text = _analysis_text(analysis)
    monitor_terms = (
        "servidor de monitoramento",
        "monitoring server",
        "site omd",
        "omd status",
        "livestatus",
        "automation-helper",
        "rrdcached",
        "nagios",
        "cmc",
    )
    checkmk_terms = ("checkmk", "check_mk", "omd", "livestatus")
    if any(term in text for term in monitor_terms) and any(term in text for term in checkmk_terms):
        return True, "a causa provável foi localizada no plano de monitoramento/OMD"
    return False, "nenhuma evidência exige troca para o servidor de monitoramento"


def _compact_host_result(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    evidence: list[dict[str, Any]] = []
    for item in list(result.get("evidence") or [])[-8:]:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "tool": item.get("tool"),
                "purpose": item.get("purpose"),
                "status": item.get("status"),
                "exit_code": item.get("exit_code"),
                "stdout": str(item.get("stdout") or "")[-900:],
                "stderr": str(item.get("stderr") or "")[-400:],
            }
        )
    return redact_object(
        {
            "investigation_id": result.get("investigation_id"),
            "hostname": result.get("hostname"),
            "analysis": {
                "status": analysis.get("status"),
                "confidence": analysis.get("confidence"),
                "summary": analysis.get("summary"),
                "conclusion": analysis.get("conclusion"),
                "probable_cause": analysis.get("probable_cause"),
                "root_cause": analysis.get("root_cause"),
                "facts": analysis.get("facts") or [],
                "proposed_actions": analysis.get("proposed_actions") or [],
            },
            "evidence": evidence,
        }
    )


def _site_scope(
    *,
    site_id: str,
    client_alias: str,
    entry_address: str,
    host_name: str,
    internal_target: str | None,
    original_strategy: str,
    correction_context: str,
    cross_host: bool,
) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "client_alias": client_alias,
        "entry_address": entry_address,
        "host_name": host_name,
        "internal_address": internal_target,
        "target_strategy": "entry_context" if correction_context == "monitoring_entry" else original_strategy,
        "original_target_strategy": original_strategy,
        "correction_context": correction_context,
        "cross_host": bool(cross_host),
        "isolated": True,
        "same_site_only": True,
    }


def _persist_scope(result: dict[str, Any], scope: dict[str, Any], flow: dict[str, Any]) -> None:
    result["site_scope"] = scope
    result["cross_host_flow"] = flow
    analysis = dict(result.get("analysis") or {})
    analysis["site_scope"] = scope
    analysis["cross_host_flow"] = flow
    result["analysis"] = analysis
    investigation_id = str(result.get("investigation_id") or "").strip()
    if investigation_id:
        update_investigation_analysis(investigation_id, redact_object(analysis))


def run_site_scoped_target(
    entry_reference: str,
    objective: str,
    *,
    site_id: str,
    client_alias: str,
    host_name: str,
    internal_target: str | None,
    target_strategy: str,
    environment: EnvironmentType = EnvironmentType.UNKNOWN,
    provider_name: str | None = None,
    model_name: str | None = None,
    playbook_mode: str = "auto",
    playbook_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Investiga e corrige preservando a fronteira ``site -> host -> monitor``.

    O primeiro hop sempre é o servidor de entrada do cliente. Em estratégia
    ``internal_ssh`` a IA entra no host afetado. Se as evidências mostrarem que
    a causa/correção está no plano Checkmk/OMD, a investigação continua no
    servidor de monitoramento do MESMO site. Essa decisão e a rota ficam
    persistidas para que a fase de correção aprovada reconstrua o mesmo caminho.
    """

    settings = settings or get_settings()
    target_environment = _environment(environment)
    requested_provider = str(provider_name or settings.ai_provider or "gemini").strip().lower()
    selection = (
        resolve_automatic_provider(settings)
        if requested_provider == "auto"
        else _explicit_provider_resolution(provider_name, model_name, settings)
    )
    effective_playbook_mode = "manual" if playbook_id else ("auto" if requested_provider == "auto" else playbook_mode)
    effective_playbook_id = playbook_id or None

    with use_investigation_budget() as budget, use_provider(selection.provider, selection.model), use_playbook(
        effective_playbook_mode, effective_playbook_id
    ):
        entry = resolve_target(
            entry_reference,
            EnvironmentType.UNKNOWN,
            22,
            settings=settings,
        )
        parent = build_executor(entry, settings=settings)
        nested: NestedSSHExecutor | None = None
        connection: dict[str, Any] = {}
        try:
            report_progress(
                "ssh_connection",
                detail=f"Abrindo o contexto do cliente {client_alias} ({site_id}) pela entrada {entry.host}.",
                access_step="site_entry",
                site_id=site_id,
                client_alias=client_alias,
                percent=32,
            )
            parent.connect()
            connection = dict(getattr(parent, "connection_metadata", {}) or {})
            connection.update(
                {
                    "site_id": site_id,
                    "client_alias": client_alias,
                    "entry_address": entry.host,
                    "internal_target": internal_target,
                    "scope_guard": f"site:{site_id}",
                    "target_strategy": target_strategy,
                }
            )

            executor = parent
            if target_strategy == "internal_ssh":
                if not internal_target:
                    raise RuntimeError("host interno sem endereco no inventario do site")
                if not isinstance(parent, VPNMenuSSHExecutor):
                    raise RuntimeError("salto para IP interno exige uma sessao VPN Menu do mesmo cliente")
                nested = NestedSSHExecutor(
                    parent,
                    host=str(internal_target),
                    port=22,
                    username=settings.ssh_default_user,
                    password=get_secret("SSH_DEFAULT_PASSWORD", settings.ssh_default_password, settings=settings),
                    route={
                        "id": f"checkmk:{site_id}:{host_name}",
                        "route_path": [entry.host, str(internal_target)],
                        "hops": 1,
                        "site_id": site_id,
                    },
                    connect_timeout=settings.ssh_connect_timeout,
                    strict_host_key_checking=settings.ssh_strict_host_key_checking,
                )
                report_progress(
                    "ssh_connection",
                    detail=f"Acessando {host_name} ({internal_target}) somente dentro do contexto {site_id}.",
                    access_step="site_internal_host",
                    site_id=site_id,
                    host=host_name,
                    internal_target=internal_target,
                    via_host=entry.host,
                    percent=44,
                )
                nested.connect()
                executor = nested
                connection.update(dict(nested.connection_metadata or {}))
            else:
                report_progress(
                    "ssh_connection",
                    status="completed",
                    detail=f"Usando o servidor de monitoramento de {client_alias} para investigar {host_name}.",
                    access_step="site_entry_context",
                    site_id=site_id,
                    percent=46,
                )

            result = run_dynamic_investigation(
                executor=executor,
                target=str(internal_target or entry_reference),
                context=objective,
                environment=target_environment,
                mode="propose",
                approve=False,
            )
            result["connection"] = connection

            follow_monitor, follow_reason = (
                _monitoring_followup_needed(result)
                if target_strategy == "internal_ssh"
                else (False, "investigação já está no servidor de monitoramento")
            )
            origin_id = str(result.get("investigation_id") or "") or None
            flow = {
                "site_id": site_id,
                "client_alias": client_alias,
                "alert_host": host_name,
                "alert_address": internal_target,
                "entry_address": entry.host,
                "host_investigated": target_strategy == "internal_ssh",
                "monitoring_followup": follow_monitor,
                "monitoring_followup_reason": follow_reason,
                "origin_investigation_id": origin_id,
                "steps": [
                    {"context": "site_entry", "target": entry.host, "status": "connected"},
                    *(
                        [{"context": "affected_host", "target": internal_target, "status": "investigated"}]
                        if target_strategy == "internal_ssh"
                        else []
                    ),
                ],
            }

            if follow_monitor:
                report_progress(
                    "ssh_connection",
                    detail=(
                        f"A causa exige o servidor de monitoramento de {client_alias}. "
                        f"Voltando ao contexto {site_id} sem sair do cliente."
                    ),
                    access_step="site_monitoring_followup",
                    site_id=site_id,
                    host=entry.host,
                    percent=74,
                )
                host_findings = _compact_host_result(result)
                monitor_objective = (
                    f"{objective}\n\n"
                    "CONTINUAÇÃO MULTICONTEXTO DO MESMO INCIDENTE. A primeira etapa investigou o host afetado "
                    f"{host_name} ({internal_target or 'sem IP'}) e concluiu que o próximo passo pertence ao servidor "
                    f"de monitoramento do cliente {client_alias}, site {site_id}. Trabalhe SOMENTE neste contexto de cliente. "
                    "Confirme no Checkmk/OMD a causa apontada pelo host, produza a correção estruturada segura quando aplicável "
                    "e deixe a validação final do sensor original para o reconciliador Checkmk. Não acesse outro site/cliente.\n\n"
                    "EVIDÊNCIAS DA ETAPA NO HOST:\n"
                    + json.dumps(host_findings, ensure_ascii=False, default=str)
                )
                monitor_result = run_dynamic_investigation(
                    executor=parent,
                    target=str(entry.host),
                    context=monitor_objective,
                    environment=EnvironmentType.MONITORING,
                    mode="propose",
                    approve=False,
                )
                monitor_result["connection"] = {
                    **dict(getattr(parent, "connection_metadata", {}) or {}),
                    "site_id": site_id,
                    "client_alias": client_alias,
                    "entry_address": entry.host,
                    "scope_guard": f"site:{site_id}",
                    "target_strategy": "entry_context",
                    "origin_host": host_name,
                    "origin_address": internal_target,
                }
                flow["steps"].append(
                    {"context": "monitoring_entry", "target": entry.host, "status": "investigated"}
                )
                flow["monitoring_investigation_id"] = monitor_result.get("investigation_id")
                flow["correction_context"] = "monitoring_entry"
                result = monitor_result
                scope = _site_scope(
                    site_id=site_id,
                    client_alias=client_alias,
                    entry_address=str(entry.host),
                    host_name=host_name,
                    internal_target=internal_target,
                    original_strategy=target_strategy,
                    correction_context="monitoring_entry",
                    cross_host=True,
                )
            else:
                correction_context = "monitoring_entry" if target_strategy == "entry_context" else "affected_host"
                flow["correction_context"] = correction_context
                scope = _site_scope(
                    site_id=site_id,
                    client_alias=client_alias,
                    entry_address=str(entry.host),
                    host_name=host_name,
                    internal_target=internal_target,
                    original_strategy=target_strategy,
                    correction_context=correction_context,
                    cross_host=False,
                )

            _persist_scope(result, scope, flow)
            result["inventory"] = {
                "saved": True,
                "source": "checkmk_master",
                "scope": f"{site_id}:{host_name}",
                "site_id": site_id,
                "client_alias": client_alias,
                "host_name": host_name,
                "internal_address": internal_target,
                "entry_address": entry.host,
            }
            result["provider_selection"] = selection.as_dict()
            result["selected_provider"] = selection.provider
            result["selected_model"] = selection.model
            stamp_evidence_timing(result)
            enrich_investigation_result(result, settings=settings)
            enrich_incident_intelligence(result)
            finalize_result_presentation(result, settings=settings)
            snapshot = budget.snapshot()
            result["budget"] = snapshot
            analysis = dict(result.get("analysis") or {})
            analysis["budget"] = snapshot
            analysis["site_scope"] = scope
            analysis["cross_host_flow"] = flow
            result["analysis"] = analysis
            investigation_id = str(result.get("investigation_id") or "").strip()
            if investigation_id:
                update_investigation_analysis(investigation_id, redact_object(analysis))
            increment("agent_investigations", labels={"mode": "propose", "site_scoped": "true"})
            if follow_monitor:
                increment("agent_cross_host_handoffs", labels={"context": "monitoring_entry"})
            return result
        finally:
            if nested is not None:
                try:
                    nested.close()
                except Exception:
                    pass
            parent.close()
