from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.ai_providers import use_provider
from app.services.intelligent_agent import run_dynamic_investigation
from app.services.persistence import upsert_host
from app.services.playbooks import selected_playbook_ssh_port, use_playbook
from app.services.progress import report_progress
from app.services.provider_router import resolve_automatic_provider
from app.services.runner import (
    _automation_summary,
    _explicit_provider_resolution,
    build_executor,
    resolve_target,
)


def _internal_ips(identity: dict[str, Any]) -> list[str]:
    raw = identity.get("internal_ips")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    brief = identity.get("ip_brief")
    if isinstance(brief, str):
        return [line.strip() for line in brief.splitlines() if line.strip()]
    return []


def persist_result_inventory(
    result: dict[str, Any],
    *,
    resolved_host: str | None = None,
    ssh_port: int | None = None,
    saved_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra no inventário o host que já passou por descoberta SSH.

    Falha de persistência não apaga o resultado da investigação. O estado é
    devolvido em ``result['inventory']`` para diagnóstico e para a interface.
    """
    identity = dict(result.get("identity") or {})
    connection = dict((result.get("automation") or {}).get("connection") or {})
    classification = dict(result.get("environment_classification") or {})
    host = str(resolved_host or connection.get("resolved_host") or result.get("target") or "").strip()
    port = int(ssh_port or connection.get("port") or 22)
    hostname = str(identity.get("hostname") or result.get("hostname") or host).strip()
    os_name = str(identity.get("os_name") or "desconhecido").strip()
    environment = str(classification.get("environment") or "unknown").strip()
    host_type = str((saved_inventory or {}).get("host_type") or result.get("profile") or "server").strip()

    if not host:
        result["inventory"] = {
            "saved": False,
            "detail": "O endereço resolvido do alvo não estava disponível.",
        }
        return result

    try:
        row = upsert_host(
            host_type=host_type,
            vpn_ip=host,
            ssh_port=port,
            hostname=hostname,
            os_name=os_name,
            environment=environment,
            internal_ips=_internal_ips(identity),
        )
        result["inventory"] = {
            "saved": True,
            "id": str(row.id),
            "vpn_ip": host,
            "ssh_port": port,
            "hostname": hostname,
            "environment": environment,
        }
    except Exception as exc:  # a investigação continua válida mesmo se o inventário falhar
        result["inventory"] = {
            "saved": False,
            "detail": f"{type(exc).__name__}: {exc}",
            "vpn_ip": host,
            "ssh_port": port,
        }
    return result


def run_target_tracked(
    reference: str,
    objective: str,
    *,
    environment: EnvironmentType = EnvironmentType.UNKNOWN,
    mode: str = "propose",
    approve: bool = False,
    ssh_port: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    playbook_mode: str = "auto",
    playbook_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Executa o motor atual emitindo etapas reais para a interface web."""
    settings = settings or get_settings()
    requested_provider = str(provider_name or settings.ai_provider or "gemini").strip().lower()
    automatic = requested_provider == "auto"

    report_progress(
        "provider_validation",
        detail="Validando credencial, endpoint e modelo da IA selecionada.",
    )
    if automatic:
        selection = resolve_automatic_provider(settings)
        effective_mode = "propose"
        effective_approve = False
        effective_playbook_mode = "auto"
        effective_playbook_id = None
    else:
        selection = _explicit_provider_resolution(provider_name, model_name, settings)
        effective_mode = mode
        effective_approve = approve
        effective_playbook_mode = playbook_mode
        effective_playbook_id = playbook_id

    report_progress(
        "provider_selected",
        status="completed",
        detail=f"{selection.label} · {selection.model or 'modelo padrão'}",
        provider=selection.provider,
        model=selection.model,
    )

    with use_provider(selection.provider, selection.model), use_playbook(
        effective_playbook_mode,
        effective_playbook_id,
    ):
        report_progress(
            "target_resolution",
            detail="Resolvendo inventário, porta SSH e playbook aplicável.",
        )
        playbook_ssh_port, selected_playbook_id = selected_playbook_ssh_port(
            objective.strip() or "validar a saúde geral do servidor"
        )
        target = resolve_target(
            reference,
            environment,
            ssh_port,
            playbook_ssh_port=playbook_ssh_port,
            settings=settings,
        )
        report_progress(
            "target_resolved",
            status="completed",
            detail=f"{target.host}:{target.port}",
            playbook_id=selected_playbook_id,
        )

        executor = build_executor(target, settings=settings)
        try:
            report_progress(
                "ssh_connection",
                detail="Abrindo a conexão SSH e validando a identidade do host.",
            )
            executor.connect()
            report_progress(
                "ssh_connected",
                status="completed",
                detail="Conexão autenticada. Nenhuma correção foi executada.",
            )
            report_progress(
                "evidence_analysis",
                detail="Descobrindo o host, coletando evidências e replanejando a análise.",
            )
            result = run_dynamic_investigation(
                executor=executor,
                target=reference,
                context=objective,
                environment=target.environment,
                mode=effective_mode,
                approve=effective_approve,
            )
        finally:
            executor.close()

    report_progress(
        "result_persistence",
        detail="Persistindo investigação e atualizando o inventário aprendido.",
    )
    result["provider_selection"] = selection.as_dict()
    result["selected_provider"] = selection.provider
    result["selected_model"] = selection.model
    result["automation"] = _automation_summary(
        selection=selection,
        target=target,
        result=result,
    )
    persist_result_inventory(
        result,
        resolved_host=target.host,
        ssh_port=target.port,
        saved_inventory=target.inventory,
    )
    report_progress(
        "completed",
        status="completed",
        detail="Investigação concluída, registrada no histórico e disponível para revisão.",
        investigation_id=result.get("investigation_id"),
    )
    return result
