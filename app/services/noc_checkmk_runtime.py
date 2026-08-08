from __future__ import annotations

import re
import shlex
import time
from datetime import datetime, timezone
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.persistence import resolve_saved_target
from app.services.redaction import redact_text
from app.services.runner import build_executor, resolve_target


_SAFE_HOST = re.compile(r"^[A-Za-z0-9_.:@+-]{1,255}$")
_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9À-ÿ _./:@%+()\[\]-]{1,255}$", re.UNICODE)
_STATE_MAP = {0: "healthy", 1: "attention", 2: "critical", 3: "inconclusive"}


class CheckmkRuntimeError(RuntimeError):
    pass


def _safe_host(value: Any) -> str:
    host = str(value or "").strip()
    if not _SAFE_HOST.fullmatch(host) or host.startswith("-"):
        raise CheckmkRuntimeError("hostname Checkmk inválido para revalidação")
    return host


def _safe_service(value: Any) -> str:
    service = " ".join(str(value or "").strip().split())
    if not _SAFE_SERVICE.fullmatch(service):
        raise CheckmkRuntimeError("serviço Checkmk contém caracteres não permitidos para revalidação")
    return service


def _monitor_reference(incident: dict[str, Any]) -> dict[str, Any] | None:
    host = str(incident.get("host") or "").strip()
    site = str(incident.get("site") or "").strip()
    for reference in (host, site):
        if not reference:
            continue
        target = resolve_saved_target(reference, environment=EnvironmentType.MONITORING.value)
        if target:
            return target
    return None


def _inner_query(host: str, service: str, *, force: bool) -> str:
    force_part = ""
    if force:
        force_part = (
            "now=$(date +%s); "
            f"lq \"COMMAND [$now] SCHEDULE_FORCED_SVC_CHECK;{host};{service};$now\" >/dev/null 2>&1 || true; "
            "sleep 2; "
        )
    query = (
        "GET services\\n"
        "Columns: host_name description state last_check plugin_output\\n"
        f"Filter: host_name = {host}\\n"
        f"Filter: description = {service}"
    )
    return force_part + f"lq {shlex.quote(query)} 2>/dev/null | sed 's/^/NOC_STATE|/'"


def _runtime_command(host: str, service: str, *, force: bool) -> str:
    inner = _inner_query(host, service, force=force)
    quoted_inner = shlex.quote(inner)
    return (
        "found=0; "
        "if command -v omd >/dev/null 2>&1; then "
        "for s in $(omd sites --bare 2>/dev/null); do found=1; echo \"NOC_CONTEXT|local|$s\"; "
        f"su - \"$s\" -c {quoted_inner} 2>&1; done; fi; "
        "if command -v docker >/dev/null 2>&1; then "
        "for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -Ei 'checkmk|check-mk'); do "
        "for s in $(docker exec \"$c\" omd sites --bare 2>/dev/null); do found=1; echo \"NOC_CONTEXT|$c|$s\"; "
        f"docker exec \"$c\" su - \"$s\" -c {quoted_inner} 2>&1; done; done; fi; "
        "[ \"$found\" -eq 1 ] || echo 'NOC_NO_SITE'"
    )


def _parse_output(output: str) -> dict[str, Any]:
    context: tuple[str | None, str | None] = (None, None)
    matches: list[dict[str, Any]] = []
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if line.startswith("NOC_CONTEXT|"):
            parts = line.split("|", 2)
            context = (parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None)
            continue
        if not line.startswith("NOC_STATE|"):
            continue
        payload = line[len("NOC_STATE|") :]
        parts = payload.split(";", 4)
        if len(parts) < 4:
            continue
        try:
            state = int(parts[2])
        except ValueError:
            continue
        try:
            last_check = int(parts[3])
        except ValueError:
            last_check = 0
        matches.append(
            {
                "container": context[0],
                "site": context[1],
                "host": parts[0],
                "service": parts[1],
                "state": state,
                "status": _STATE_MAP.get(state, "inconclusive"),
                "last_check": last_check,
                "plugin_output": parts[4] if len(parts) > 4 else "",
            }
        )
    if not matches:
        return {"found": False, "status": "inconclusive", "state": None, "matches": []}
    selected = sorted(matches, key=lambda item: int(item.get("last_check") or 0), reverse=True)[0]
    return {"found": True, **selected, "matches": matches}


def query_incident_service(
    incident: dict[str, Any],
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    host = _safe_host(incident.get("host"))
    service = _safe_service(incident.get("service"))
    resolved = _monitor_reference(incident)
    if not resolved:
        return {
            "available": False,
            "found": False,
            "status": "inconclusive",
            "reason": "não existe mapeamento persistido do host/site para um servidor de monitoramento",
            "forced": force,
        }

    target = resolve_target(
        str(resolved.get("vpn_ip") or ""),
        EnvironmentType.MONITORING,
        int(resolved.get("ssh_port") or 22),
        settings=settings,
    )
    executor = build_executor(target, settings=settings)
    started = time.monotonic()
    try:
        executor.connect()
        result = executor.run_sudo(
            _runtime_command(host, service, force=force),
            EnvironmentType.MONITORING,
            timeout=max(20, int(settings.noc_checkmk_recheck_timeout_seconds)),
        )
        parsed = _parse_output(result.stdout)
        return {
            "available": True,
            "forced": force,
            "monitor_target": str(resolved.get("vpn_ip") or ""),
            "monitor_hostname": resolved.get("hostname"),
            "exit_code": result.exit_code,
            "stderr": redact_text(result.stderr),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.monotonic() - started) * 1000),
            **parsed,
        }
    except Exception as exc:
        return {
            "available": True,
            "forced": force,
            "found": False,
            "status": "inconclusive",
            "reason": f"{type(exc).__name__}: {exc}",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        executor.close()


def is_green(runtime: dict[str, Any]) -> bool:
    return bool(runtime.get("found")) and int(runtime.get("state") if runtime.get("state") is not None else -1) == 0
