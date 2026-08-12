from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType
from app.services.checkmk_master import site_and_host
from app.services.noc_skills import select_noc_skill


def _environment(value: str | None) -> str:
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value)).value
    except ValueError:
        return EnvironmentType.UNKNOWN.value


def resolve_checkmk_site_target(event: dict[str, Any]) -> dict[str, Any]:
    """Resolve um alerta sem permitir mistura entre clientes.

    Um IP interno nunca e pesquisado globalmente. Primeiro selecionamos o
    ``site_id`` e somente depois o host pertencente a esse site.
    """

    site_id = str(event.get("site_id") or event.get("site") or "").strip()
    host_name = str(event.get("host") or "").strip()
    if not site_id:
        return {
            "valid": False,
            "reason": "alerta sem site_id; isolamento do cliente nao pode ser garantido",
            "auto_investigate": False,
        }

    site, host = site_and_host(site_id, host_name)
    if not site:
        return {
            "valid": False,
            "site_id": site_id,
            "reason": "site nao existe no inventario sincronizado do CMK05",
            "auto_investigate": False,
        }
    if not site.enabled:
        return {
            "valid": False,
            "site_id": site_id,
            "client_alias": site.alias,
            "reason": "site esta desativado no Checkmk master",
            "auto_investigate": False,
        }

    host_address = str(
        (host.internal_address if host else None)
        or event.get("host_address")
        or ""
    ).strip()
    host_kind = str(host.host_kind if host else "server")
    environment = _environment(host.environment if host else None)
    skill = select_noc_skill({**event, "host_kind": host_kind, "host_address": host_address}, host_kind=host_kind)
    strategy = str(skill.get("target_strategy") or "internal_ssh")

    if site.shared_endpoint:
        return {
            "valid": True,
            "auto_investigate": False,
            "reason": "endpoint de monitoramento compartilhado; rota interna especifica do site ainda precisa ser validada",
            "site_id": site.site_id,
            "client_alias": site.alias,
            "entry_address": site.livestatus_host,
            "livestatus_port": site.livestatus_port,
            "status_host": site.status_host,
            "shared_endpoint": True,
            "host_name": host_name,
            "internal_address": host_address or None,
            "host_kind": host_kind,
            "environment": environment,
            "strategy": "site_guard",
            "skill": skill,
        }

    if not site.livestatus_host:
        return {
            "valid": False,
            "auto_investigate": False,
            "site_id": site.site_id,
            "client_alias": site.alias,
            "reason": "site sem endereco de entrada no CMK05",
            "skill": skill,
        }

    if strategy == "internal_ssh" and host_address in {"", "0.0.0.0", "127.0.0.1", "::1"}:
        strategy = "entry_context"

    return {
        "valid": True,
        "auto_investigate": True,
        "site_id": site.site_id,
        "client_alias": site.alias,
        "entry_address": site.livestatus_host,
        "livestatus_port": site.livestatus_port,
        "status_site": site.status_site,
        "status_host": site.status_host,
        "shared_endpoint": False,
        "host_name": host_name,
        "internal_address": host_address or None,
        "host_kind": host_kind,
        "environment": environment,
        "strategy": strategy,
        "skill": skill,
        "scope_key": f"{site.site_id}:{host_name}",
    }
