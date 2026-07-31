from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.ai_providers import use_provider
from app.services.intelligent_agent import run_dynamic_investigation
from app.services.persistence import resolve_saved_target
from app.services.playbooks import selected_playbook_ssh_port, use_playbook
from app.services.provider_preflight import require_selected_provider
from app.services.provider_router import ProviderResolution, resolve_automatic_provider
from app.services.runtime_env import runtime_int, runtime_value
from app.services.secrets import get_secret
from app.services.ssh import SSHExecutor
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


@dataclass(frozen=True)
class ResolvedTarget:
    reference: str
    host: str
    port: int
    environment: EnvironmentType
    inventory: dict[str, Any] | None


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def resolve_target(
    reference: str,
    environment: EnvironmentType = EnvironmentType.UNKNOWN,
    ssh_port: int | None = None,
    *,
    playbook_ssh_port: int | None = None,
    settings: Settings | None = None,
) -> ResolvedTarget:
    settings = settings or get_settings()
    explicit_port = _validate_ssh_port(ssh_port, "informada")
    selected_playbook_port = _validate_ssh_port(playbook_ssh_port, "do playbook")
    default_port = _validate_ssh_port(settings.ssh_default_port, "padrão")
    saved = resolve_saved_target(reference, None if environment == EnvironmentType.UNKNOWN else environment.value)
    if saved:
        resolved_environment = environment
        if resolved_environment == EnvironmentType.UNKNOWN:
            try:
                resolved_environment = EnvironmentType(saved.get("environment") or EnvironmentType.UNKNOWN.value)
            except ValueError:
                resolved_environment = EnvironmentType.UNKNOWN
        saved_port = _validate_ssh_port(saved.get("ssh_port"), "do inventário")
        port = explicit_port or selected_playbook_port or saved_port or default_port
        return ResolvedTarget(reference, str(saved["vpn_ip"]), port, resolved_environment, saved)
    if _is_ip(reference):
        return ResolvedTarget(
            reference,
            reference,
            explicit_port or selected_playbook_port or default_port,
            environment,
            None,
        )
    raise LookupError(f"alvo '{reference}' não existe no inventário; na primeira execução informe o IP VPN")


def _validate_ssh_port(value: Any, source: str) -> int | None:
    if value is None:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"porta SSH {source} é inválida: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"porta SSH {source} deve estar entre 1 e 65535")
    return port


def _ssh_access_mode(settings: Settings) -> str:
    configured = str(
        runtime_value("SSH_ACCESS_MODE", "", settings=settings)
        or runtime_value("SSH_BASTION_MODE", "", settings=settings)
        or runtime_value("SSH_VPN_ACCESS_MODE", "", settings=settings)
        or ""
    ).strip().casefold()
    if configured in {"direct", "direct-tcpip", "tcp", "jump"}:
        return "direct"
    if configured in {"vpn", "vpn-menu", "vpn_menu", "menu", "interactive"}:
        return "vpn_menu"
    return "vpn_menu" if settings.ssh_bastion_host else "direct"


def build_executor(target: ResolvedTarget, *, settings: Settings | None = None) -> SSHExecutor:
    settings = settings or get_settings()
    common: dict[str, Any] = {
        "host": target.host,
        "port": target.port,
        "username": settings.ssh_default_user,
        "password": get_secret("SSH_DEFAULT_PASSWORD", settings.ssh_default_password, settings=settings),
        "connect_timeout": settings.ssh_connect_timeout,
        "private_key_path": settings.ssh_private_key_path,
        "private_key_passphrase": get_secret(
            "SSH_PRIVATE_KEY_PASSPHRASE",
            settings.ssh_private_key_passphrase,
            settings=settings,
        ),
        "allow_agent": settings.ssh_allow_agent,
        "look_for_keys": settings.ssh_look_for_keys,
        "strict_host_key_checking": settings.ssh_strict_host_key_checking,
        "known_hosts_path": settings.ssh_known_hosts_path,
        "bastion_host": settings.ssh_bastion_host,
        "bastion_port": settings.ssh_bastion_port,
        "bastion_user": settings.ssh_bastion_user,
        "bastion_password": get_secret(
            "SSH_BASTION_PASSWORD",
            settings.ssh_bastion_password,
            settings=settings,
        ),
        "bastion_private_key_path": settings.ssh_bastion_private_key_path,
        "bastion_private_key_passphrase": get_secret(
            "SSH_BASTION_PRIVATE_KEY_PASSPHRASE",
            settings.ssh_bastion_private_key_passphrase,
            settings=settings,
        ),
    }
    if settings.ssh_bastion_host and _ssh_access_mode(settings) == "vpn_menu":
        firewall_password = runtime_value("SSH_FIREWALL_PF_PASSWORD", None, settings=settings)
        return VPNMenuSSHExecutor(
            **common,
            vpn_command=str(runtime_value("SSH_VPN_COMMAND", "vpn {host}", settings=settings) or "vpn {host}"),
            vpn_menu_timeout=runtime_int(
                "SSH_VPN_MENU_TIMEOUT",
                45,
                minimum=10,
                maximum=300,
                settings=settings,
            ),
            firewall_user=str(runtime_value("SSH_FIREWALL_PF_USER", "root", settings=settings) or "root").strip() or "root",
            firewall_password=get_secret(
                "SSH_FIREWALL_PF_PASSWORD",
                str(firewall_password) if firewall_password is not None else None,
                settings=settings,
            ),
            firewall_port=runtime_int("SSH_FIREWALL_PF_PORT", 2224, settings=settings),
            firewall_shell_option=runtime_int(
                "SSH_FIREWALL_PF_SHELL_OPTION",
                8,
                minimum=0,
                maximum=99,
                settings=settings,
            ),
        )
    return SSHExecutor(**common)


def _explicit_provider_resolution(
    provider_name: str | None,
    model_name: str | None,
    settings: Settings,
) -> ProviderResolution:
    with use_provider(provider_name, model_name):
        preflight = require_selected_provider(settings)
    provider = str(
        getattr(preflight, "provider", None)
        or provider_name
        or getattr(settings, "ai_provider", "gemini")
        or "gemini"
    )
    model = str(getattr(preflight, "model", None) or model_name or "")
    return ProviderResolution(
        provider=provider,
        model=model,
        label=str(getattr(preflight, "label", provider)),
        detail=str(getattr(preflight, "detail", "Provedor validado.")),
        automatic=False,
        attempts=(
            {
                "phase": "full_preflight",
                "provider": provider,
                "model": model,
                "state": str(getattr(getattr(preflight, "state", None), "value", "available")),
                "selectable": bool(getattr(preflight, "selectable", True)),
                "detail": str(getattr(preflight, "detail", "Provedor validado.")),
                "latency_ms": getattr(preflight, "latency_ms", None),
            },
        ),
    )


def _automation_summary(
    *,
    selection: ProviderResolution,
    target: ResolvedTarget,
    result: dict[str, Any],
) -> dict[str, Any]:
    classification = result.get("environment_classification") or {}
    environment = str(classification.get("environment") or target.environment.value)
    playbook = result.get("playbook") or {}
    evidence_count = len(result.get("evidence") or [])
    proposed_actions = list((result.get("analysis") or {}).get("proposed_actions") or [])
    approval_token = result.get("approval_token")
    intelligence = result.get("intelligence") or {}
    connection = dict(result.get("connection") or {})
    connection_port = int(connection.get("ssh_port") or target.port)
    return {
        "mode": "safe_autopilot" if selection.automatic else "guided",
        "status": "completed",
        "provider": selection.as_dict(),
        "connection": {
            "target": target.reference,
            "resolved_host": target.host,
            "port": connection_port,
            "through_bastion": bool(get_settings().ssh_bastion_host),
            **connection,
        },
        "environment": environment,
        "playbook": {
            "id": playbook.get("id"),
            "title": playbook.get("title"),
            "selection": "advisory" if intelligence.get("playbook_role") == "advisory" else "configured",
        },
        "intelligence": {
            "enabled": bool(intelligence.get("enabled")),
            "loop": intelligence.get("loop"),
            "critic_verdict": (intelligence.get("critic") or {}).get("verdict"),
            "provider_failover": bool(intelligence.get("provider_failover")),
        },
        "evidence_count": evidence_count,
        "proposal_count": len(proposed_actions),
        "human_approval_available": bool(approval_token),
        "phases": [
            {"name": "provider_selection", "status": "completed", "detail": selection.detail},
            {"name": "mission_interpretation", "status": "completed", "detail": "Objetivo convertido em missão e critérios verificáveis."},
            {"name": "target_resolution", "status": "completed", "detail": f"{target.host}:{connection_port}"},
            {
                "name": "ssh_access",
                "status": "completed",
                "detail": (
                    f"Acesso via menu VPN: {connection.get('client_name')}."
                    if connection.get("client_name")
                    else "Acesso autenticado e chave de host validada."
                ),
            },
            {"name": "environment_classification", "status": "completed", "detail": environment},
            {"name": "adaptive_reasoning", "status": "completed", "detail": "Planejamento, execução, observação, reflexão e replanejamento."},
            {"name": "evidence_collection", "status": "completed", "detail": f"{evidence_count} evidência(s) coletada(s)."},
            {"name": "independent_critic", "status": "completed", "detail": str((intelligence.get("critic") or {}).get("verdict") or "não disponível")},
            {"name": "solution_proposal", "status": "completed", "detail": f"{len(proposed_actions)} ação(ões) proposta(s)."},
            {
                "name": "corrective_execution",
                "status": "approval_required" if approval_token else "not_executed",
                "detail": "Nenhuma correção é executada pelo autopilot sem aprovação humana separada.",
            },
        ],
        "safety": {
            "production_changes": "blocked",
            "standby_changes": "blocked",
            "reboot_shutdown": "blocked",
            "customer_databases": "blocked",
            "container_lifecycle": "blocked",
            "corrections": "human_approval_and_second_ai_required",
        },
    }


def run_target(
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
    """Executa investigação guiada ou autopilot seguro no mesmo motor operacional."""
    settings = settings or get_settings()
    requested_provider = str(provider_name or settings.ai_provider or "gemini").strip().lower()
    automatic = requested_provider == "auto"

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

    with use_provider(selection.provider, selection.model), use_playbook(
        effective_playbook_mode,
        effective_playbook_id,
    ):
        playbook_ssh_port, _ = selected_playbook_ssh_port(
            objective.strip() or "validar a saúde geral do servidor"
        )
        target = resolve_target(
            reference,
            environment,
            ssh_port,
            playbook_ssh_port=playbook_ssh_port,
            settings=settings,
        )
        executor = build_executor(target, settings=settings)
        try:
            executor.connect()
            result = run_dynamic_investigation(
                executor=executor,
                target=reference,
                context=objective,
                environment=target.environment,
                mode=effective_mode,
                approve=effective_approve,
            )
            result["connection"] = dict(getattr(executor, "connection_metadata", {}) or {})
        finally:
            executor.close()

    result["provider_selection"] = selection.as_dict()
    result["selected_provider"] = selection.provider
    result["selected_model"] = selection.model
    result["automation"] = _automation_summary(
        selection=selection,
        target=target,
        result=result,
    )
    return result
