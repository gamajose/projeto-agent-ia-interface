from __future__ import annotations

from typing import Any

from app.services.noc_action_policy import policy_allows_autonomous_correction


_INSTALLED = False


def install_noc_policy_guard() -> None:
    """Coloca a política por categoria antes do gate L4 existente.

    A investigação continua somente leitura para qualquer alerta. Esta camada
    controla exclusivamente se o Supervisor pode chegar ao self-healing.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    from app.services import noc_supervisor

    original = noc_supervisor._autonomy_eligible
    if getattr(original, "_noc_category_guard", False):
        _INSTALLED = True
        return

    def guarded(incident: dict[str, Any], result: dict[str, Any], settings):
        analysis = dict(result.get("analysis") or {})
        event = {
            "site_id": incident.get("site") or incident.get("site_id"),
            "host": incident.get("host"),
            "service": incident.get("service"),
            "output": incident.get("last_output") or analysis.get("summary") or "",
            "skill_id": (result.get("metadata") or {}).get("skill_id") if isinstance(result.get("metadata"), dict) else None,
        }
        allowed, category, reason = policy_allows_autonomous_correction(event)
        if not allowed:
            return False, f"{reason} [categoria={category}]"
        return original(incident, result, settings)

    guarded._noc_category_guard = True  # type: ignore[attr-defined]
    noc_supervisor._autonomy_eligible = guarded
    _INSTALLED = True
