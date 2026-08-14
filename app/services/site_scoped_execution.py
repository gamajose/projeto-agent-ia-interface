from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings
from app.services.nested_ssh import NestedSSHExecutor
from app.services.runner import build_executor, resolve_target
from app.services.secrets import get_secret
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


_MONITOR_COMMAND_RE = re.compile(
    r"(?:\bomd\s+(?:status|start|restart|sites)\b|docker\s+exec\b.*\bomd\b)",
    re.IGNORECASE,
)


class SiteScopedCorrectionExecutor:
    """Executor que preserva o contexto de um único cliente/site.

    ``parent`` é sempre o servidor de entrada/monitoramento do site. ``nested``
    existe apenas quando a correção pertence ao host interno. Comandos OMD são
    sempre roteados ao parent; os demais seguem o contexto de correção escolhido
    e persistido pela investigação.
    """

    def __init__(
        self,
        parent: Any,
        nested: NestedSSHExecutor | None,
        *,
        default_context: str,
        site_id: str,
    ) -> None:
        self.parent = parent
        self.nested = nested
        self.default_context = default_context
        self.site_id = site_id
        default = parent if default_context == "monitoring_entry" or nested is None else nested
        self.host = getattr(default, "host", getattr(parent, "host", ""))
        self.port = int(getattr(default, "port", 22) or 22)
        self.connection_metadata = {
            "site_id": site_id,
            "execution_context": default_context,
            "entry_host": getattr(parent, "host", ""),
            "internal_host": getattr(nested, "host", None) if nested else None,
            "same_site_only": True,
        }
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        self.parent.connect()
        if self.nested is not None:
            self.nested.connect()
        self._connected = True

    def _executor(self, command: str) -> Any:
        if _MONITOR_COMMAND_RE.search(str(command or "")):
            return self.parent
        if self.default_context == "monitoring_entry" or self.nested is None:
            return self.parent
        return self.nested

    def run(self, command: str, environment: EnvironmentType, approved: bool = False, timeout: int = 60):
        return self._executor(command).run(command, environment, approved=approved, timeout=timeout)

    def run_sudo(self, command: str, environment: EnvironmentType, approved: bool = False, timeout: int = 60):
        return self._executor(command).run_sudo(command, environment, approved=approved, timeout=timeout)

    def close(self) -> None:
        if self.nested is not None:
            try:
                self.nested.close()
            except Exception:
                pass
        try:
            self.parent.close()
        finally:
            self._connected = False


@dataclass(frozen=True)
class ApprovedExecutionRoute:
    executor: Any
    ssh_port: int
    site_scoped: bool
    context: str
    metadata: dict[str, Any]


def build_approved_execution_route(
    investigation: dict[str, Any],
    analysis: dict[str, Any],
    *,
    environment: EnvironmentType,
    approved_ssh_port: int | None,
    settings: Settings,
) -> ApprovedExecutionRoute:
    """Reconstrói o mesmo envelope de acesso usado na investigação.

    Jobs comuns mantêm o comportamento antigo. Investigações do Checkmk que
    persistiram ``analysis.site_scope`` nunca fazem lookup global do IP interno:
    entram primeiro pelo endpoint do próprio site e, se necessário, criam um
    NestedSSHExecutor somente para o host pertencente àquele site.
    """

    target_reference = str(investigation.get("target") or "")
    scope = dict(analysis.get("site_scope") or {})
    if not scope.get("isolated") or not scope.get("site_id") or not scope.get("entry_address"):
        target = resolve_target(
            target_reference,
            environment,
            approved_ssh_port,
            settings=settings,
        )
        executor = build_executor(target, settings=settings)
        return ApprovedExecutionRoute(
            executor=executor,
            ssh_port=int(getattr(target, "port", approved_ssh_port or 22) or 22),
            site_scoped=False,
            context="direct",
            metadata={"target": target_reference},
        )

    site_id = str(scope.get("site_id") or "").strip()
    entry_address = str(scope.get("entry_address") or "").strip()
    internal_address = str(scope.get("internal_address") or "").strip() or None
    correction_context = str(scope.get("correction_context") or "").strip()
    strategy = str(scope.get("target_strategy") or scope.get("original_target_strategy") or "internal_ssh").strip()
    if correction_context not in {"affected_host", "monitoring_entry"}:
        correction_context = "monitoring_entry" if strategy == "entry_context" else "affected_host"

    entry = resolve_target(entry_address, EnvironmentType.UNKNOWN, 22, settings=settings)
    parent = build_executor(entry, settings=settings)
    nested: NestedSSHExecutor | None = None

    if correction_context == "affected_host":
        if not internal_address:
            raise RuntimeError("correção site-scoped exige IP interno do host afetado")
        if not isinstance(parent, VPNMenuSSHExecutor):
            raise RuntimeError("correção no host interno exige a sessão VPN Menu do mesmo cliente")
        nested = NestedSSHExecutor(
            parent,
            host=internal_address,
            port=22,
            username=settings.ssh_default_user,
            password=get_secret("SSH_DEFAULT_PASSWORD", settings.ssh_default_password, settings=settings),
            route={
                "id": f"approved:{site_id}:{scope.get('host_name') or internal_address}",
                "route_path": [entry.host, internal_address],
                "hops": 1,
                "site_id": site_id,
            },
            connect_timeout=settings.ssh_connect_timeout,
            strict_host_key_checking=settings.ssh_strict_host_key_checking,
        )

    executor = SiteScopedCorrectionExecutor(
        parent,
        nested,
        default_context=correction_context,
        site_id=site_id,
    )
    return ApprovedExecutionRoute(
        executor=executor,
        ssh_port=22,
        site_scoped=True,
        context=correction_context,
        metadata={
            "site_id": site_id,
            "client_alias": scope.get("client_alias"),
            "entry_address": entry_address,
            "internal_address": internal_address,
            "correction_context": correction_context,
            "cross_host": bool(scope.get("cross_host")),
            "same_site_only": True,
        },
    )
