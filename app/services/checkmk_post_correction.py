from __future__ import annotations

import ipaddress
import re
import shlex
from typing import Any

from app.core.policies import EnvironmentType
from app.services.redaction import redact_text


_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _host(value: Any) -> str:
    host = str(value or "").strip()
    if not host or not _SAFE_HOST_RE.fullmatch(host) or host.startswith("-"):
        raise ValueError("TARGET_HOST inválido para nova coleta Checkmk")
    return host


def _address(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError as exc:
        raise ValueError("TARGET_IP inválido para validação do agente Checkmk") from exc


def build_post_correction_collection_command(target_host: str, target_address: str | None = None) -> str:
    """Monta a pós-validação TARGET_HOST -> MONITORING_HOST -> CHECKMK_SITE.

    Além de localizar exatamente um site responsável pelo host, quando o IP
    interno é conhecido o próprio MONITORING_HOST confirma TCP/6556 e o cabeçalho
    ``<<<check_mk>>>`` antes de limpar cache e executar uma nova coleta. Isso
    reproduz a validação operacional já usada em campo e impede considerar a
    correção local suficiente quando o monitor ainda não alcança o agente.
    """

    host = _host(target_host)
    address = _address(target_address)
    quoted_host = shlex.quote(host)
    describe = shlex.quote(f"cmk -D {host}")
    flush = shlex.quote(f"cmk --flush {host}")
    collect = shlex.quote(f"cmk --debug -vvn {host}")

    monitor_probe = ""
    if address:
        connect_probe = shlex.quote(f"</dev/tcp/{address}/6556")
        agent_probe = shlex.quote(f"exec 3<>/dev/tcp/{address}/6556; head -n 30 <&3")
        monitor_probe = (
            f"target_ip={shlex.quote(address)}; "
            f"if ! timeout 10 bash -c {connect_probe} >/dev/null 2>&1; then "
            "echo \"MONITOR_AGENT_TCP=FAILED TARGET_IP=$target_ip PORT=6556\"; "
            "echo 'COLLECTION_STATUS=FAILED REASON=monitor_cannot_reach_agent'; exit 43; fi; "
            f"if ! timeout 15 bash -c {agent_probe} 2>/dev/null | grep -q '<<<check_mk>>>'; then "
            "echo \"MONITOR_AGENT_PAYLOAD=FAILED TARGET_IP=$target_ip PORT=6556\"; "
            "echo 'COLLECTION_STATUS=FAILED REASON=agent_payload_invalid'; exit 44; fi; "
            "echo \"MONITOR_AGENT_TCP=SUCCESS TARGET_IP=$target_ip PORT=6556\"; "
        )

    return (
        f"target={quoted_host}; matches=0; mode=''; container=''; site=''; "
        "for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -Ei 'checkmk|check-mk'); do "
        "for s in $(docker exec \"$c\" omd sites --bare 2>/dev/null); do "
        f"if docker exec \"$c\" su - \"$s\" -c {describe} >/dev/null 2>&1; then "
        "matches=$((matches+1)); mode='docker'; container=\"$c\"; site=\"$s\"; "
        "echo \"MATCH MODE=docker CONTAINER=$c SITE=$s HOST=$target\"; fi; done; done; "
        "if command -v omd >/dev/null 2>&1; then "
        "for s in $(omd sites --bare 2>/dev/null); do "
        f"if su - \"$s\" -c {describe} >/dev/null 2>&1; then "
        "matches=$((matches+1)); mode='native'; container=''; site=\"$s\"; "
        "echo \"MATCH MODE=native SITE=$s HOST=$target\"; fi; done; fi; "
        "echo \"MATCH_COUNT=$matches\"; "
        "if [ \"$matches\" -ne 1 ]; then echo 'COLLECTION_STATUS=FAILED REASON=site_not_unique'; exit 42; fi; "
        "echo \"SELECTED_MODE=$mode SELECTED_CONTAINER=$container SELECTED_SITE=$site TARGET_HOST=$target\"; "
        + monitor_probe
        + "if [ \"$mode\" = 'docker' ]; then "
        + f"docker exec \"$container\" su - \"$site\" -c {describe}; "
        + f"docker exec \"$container\" su - \"$site\" -c {flush} || exit $?; "
        + f"docker exec \"$container\" su - \"$site\" -c {collect}; rc=$?; "
        + f"if [ \"$rc\" -eq 0 ]; then docker exec \"$container\" su - \"$site\" -c {describe}; fi; "
        + "else "
        + f"su - \"$site\" -c {describe}; "
        + f"su - \"$site\" -c {flush} || exit $?; "
        + f"su - \"$site\" -c {collect}; rc=$?; "
        + f"if [ \"$rc\" -eq 0 ]; then su - \"$site\" -c {describe}; fi; "
        + "fi; "
        + "if [ \"$rc\" -eq 0 ]; then echo 'COLLECTION_STATUS=SUCCESS'; "
        + "else echo \"COLLECTION_STATUS=FAILED RC=$rc\"; fi; exit \"$rc\""
    )


def collect_target_from_monitor(
    executor: Any,
    target_host: str,
    target_address: str | None = None,
) -> dict[str, Any]:
    """Executa a pós-coleta no MONITORING_HOST do mesmo envelope site-scoped."""

    host = _host(target_host)
    address = _address(target_address)
    monitor_executor = getattr(executor, "parent", executor)
    command = build_post_correction_collection_command(host, address)
    try:
        result = monitor_executor.run_sudo(
            command,
            EnvironmentType.MONITORING,
            timeout=360,
        )
        stdout = redact_text(str(result.stdout or ""))
        stderr = redact_text(str(result.stderr or ""))
        ok = int(result.exit_code or 0) == 0 and "COLLECTION_STATUS=SUCCESS" in stdout
        if address:
            ok = ok and "MONITOR_AGENT_TCP=SUCCESS" in stdout
        return {
            "stage": "checkmk_post_correction_collection",
            "target_host": host,
            "target_address": address,
            "status": "validated" if ok else "failed",
            "exit_code": int(result.exit_code or 0),
            "stdout": stdout,
            "stderr": stderr,
            "monitoring_context": True,
            "same_site_only": True,
            "agent_reachable_from_monitor": bool(address and "MONITOR_AGENT_TCP=SUCCESS" in stdout) if address else None,
        }
    except Exception as exc:
        return {
            "stage": "checkmk_post_correction_collection",
            "target_host": host,
            "target_address": address,
            "status": "failed",
            "exit_code": 255,
            "stdout": "",
            "stderr": redact_text(f"{type(exc).__name__}: {exc}"),
            "monitoring_context": True,
            "same_site_only": True,
            "agent_reachable_from_monitor": False if address else None,
        }
