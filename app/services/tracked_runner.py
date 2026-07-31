from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.ai_providers import use_provider
from app.services.intelligent_agent import run_dynamic_investigation
from app.services.inventory_learning import learn_result_inventory
from app.services.playbooks import selected_playbook_ssh_port, use_playbook
from app.services.progress import report_progress
from app.services.provider_router import resolve_automatic_provider
from app.services.result_presentation import finalize_result_presentation
from app.services.runner import (
    _automation_summary,
    _explicit_provider_resolution,
    build_executor,
    resolve_target,
)


def persist_result_inventory(
    result: dict[str, Any],
    *,
    resolved_host: str | None = None,
    ssh_port: int | None = None,
    saved_inventory: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Registra o alvo descoberto sem invalidar a investigação em caso de falha."""
    del saved_inventory  # compatibilidade com chamadas anteriores
    inventory = learn_result_inventory(
        result,
        resolved_host=resolved_host,
        ssh_port=ssh_port,
        settings=settings or get_settings(),
    )
    result["inventory"] = inventory
    analysis = dict(result.get("analysis") or {})
    analysis["inventory"] = inventory
    result["analysis"] = analysis
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
    """Executa o motor operacional emitindo etapas canônicas para a interface."""
    settings = settings or get_settings()
    requested_provider = str(provider_name or settings.ai_provider or "gemini").strip().lower()
    automatic = requested_provider == "auto"

    report_progress(
        "provider_validation",
        detail="Validando credencial, endpoint e modelo da IA selecionada.",
        percent=8,
    )
    if automatic:
        selection = resolve_automatic_provider(settings)
        effective_mode = "propose" if mode == "correct" else mode
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
        "provider_validation",
        status="completed",
        detail=f"{selection.label} · {selection.model or 'modelo padrão'}",
        provider=selection.provider,
        model=selection.model,
        percent=18,
    )

    with use_provider(selection.provider, selection.model), use_playbook(
        effective_playbook_mode,
        effective_playbook_id,
    ):
        report_progress(
            "target_resolution",
            detail="Resolvendo inventário, porta SSH e playbook aplicável.",
            percent=22,
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
            "target_resolution",
            status="completed",
            detail=f"Alvo resolvido em {target.host}:{target.port}.",
            playbook_id=selected_playbook_id,
            percent=30,
        )

        executor = build_executor(target, settings=settings)
        connection: dict[str, Any] = {}
        effective_ssh_port = target.port
        try:
            report_progress(
                "ssh_connection",
                detail="Entrando no Monitor 1 e abrindo o cliente pelo menu VPN.",
                percent=35,
            )
            executor.connect()
            connection = dict(getattr(executor, "connection_metadata", {}) or {})
            effective_ssh_port = int(connection.get("ssh_port") or executor.port or target.port)
            client_name = str(connection.get("client_name") or "").strip()
            report_progress(
                "ssh_connection",
                status="completed",
                detail=(
                    f"Conectado via menu VPN em {client_name} ({target.host}:{effective_ssh_port})."
                    if client_name
                    else "Conexão autenticada. Nenhuma correção foi executada."
                ),
                client_name=client_name or None,
                vpn_ip=target.host,
                ssh_port=effective_ssh_port,
                percent=42,
            )
            report_progress(
                "evidence_analysis",
                detail="Descobrindo o host, coletando evidências e replanejando a análise.",
                percent=48,
            )
            result = run_dynamic_investigation(
                executor=executor,
                target=reference,
                context=objective,
                environment=target.environment,
                mode=effective_mode,
                approve=effective_approve,
            )
            result["connection"] = connection
            report_progress(
                "evidence_analysis",
                status="completed",
                detail=f"Coleta e análise concluídas com {len(result.get('evidence') or [])} evidência(s).",
                percent=88,
            )
        finally:
            executor.close()

    report_progress(
        "result_persistence",
        detail="Persistindo investigação e atualizando o inventário aprendido.",
        percent=92,
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
        ssh_port=effective_ssh_port,
        saved_inventory=target.inventory,
        settings=settings,
    )
    finalize_result_presentation(result, settings=settings)
    inventory = dict(result.get("inventory") or {})
    report_progress(
        "result_persistence",
        status="completed",
        detail=(
            "Resultado salvo e alvo incluído no inventário aprendido."
            if inventory.get("saved")
            else f"Resultado salvo; inventário pendente: {inventory.get('detail') or 'falha não detalhada'}."
        ),
        inventory_saved=bool(inventory.get("saved")),
        percent=98,
    )
    report_progress(
        "completed",
        status="completed",
        detail="Investigação concluída, registrada no histórico e disponível para revisão.",
        investigation_id=result.get("investigation_id"),
        percent=100,
    )
    return result
