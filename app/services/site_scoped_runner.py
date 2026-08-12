from __future__ import annotations

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
from app.services.playbooks import use_playbook
from app.services.progress import report_progress
from app.services.provider_router import resolve_automatic_provider
from app.services.result_presentation import finalize_result_presentation
from app.services.runner import _explicit_provider_resolution, build_executor, resolve_target
from app.services.secrets import get_secret
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


def _environment(value: str | EnvironmentType | None) -> EnvironmentType:
    if isinstance(value, EnvironmentType):
        return value
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value))
    except ValueError:
        return EnvironmentType.UNKNOWN


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
    """Investiga um alerta preservando a fronteira ``site -> host interno``.

    O primeiro hop sempre abre o servidor de entrada do mesmo cliente. Somente
    depois disso um ``NestedSSHExecutor`` pode alcançar o IP interno. Nenhuma
    busca global por IP interno e realizada.
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
        executor = parent
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

            if target_strategy == "internal_ssh":
                if not internal_target:
                    raise RuntimeError("host interno sem endereco no inventario do site")
                if not isinstance(parent, VPNMenuSSHExecutor):
                    raise RuntimeError(
                        "salto para IP interno exige uma sessao VPN Menu do mesmo cliente"
                    )
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
                    detail=(
                        f"Usando o servidor de monitoramento de {client_alias} para investigar "
                        f"{host_name} ({internal_target or 'alvo local'})."
                    ),
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
            result["site_scope"] = {
                "site_id": site_id,
                "client_alias": client_alias,
                "entry_address": entry.host,
                "host_name": host_name,
                "internal_address": internal_target,
                "target_strategy": target_strategy,
                "isolated": True,
            }
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
            analysis["site_scope"] = result["site_scope"]
            result["analysis"] = analysis
            increment("agent_investigations", labels={"mode": "propose", "site_scoped": "true"})
            return result
        finally:
            if nested is not None:
                try:
                    nested.close()
                except Exception:
                    pass
            parent.close()
