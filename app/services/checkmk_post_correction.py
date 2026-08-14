from __future__ import annotations

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


def build_post_correction_collection_command(target_host: str) -> str:
    """Monta uma coleta site-safe sem assumir TARGET_HOST == MONITORING_HOST.

    O comando roda somente no servidor de monitoramento já pertencente ao mesmo
    site/cliente. Ele procura o TARGET_HOST em Checkmk Docker e nativo, exige
    exatamente uma correspondência e só então executa describe -> flush -> vvn.
    Discovery não é executado aqui; ele continua dependendo de evidência própria.
    """

    host = _host(target_host)
    quoted_host = shlex.quote(host)
    describe = shlex.quote(f"cmk -D {host}")
    flush = shlex.quote(f"cmk --flush {host}")
    collect = shlex.quote(f"cmk --debug -vvn {host}")

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
        "if [ \"$mode\" = 'docker' ]; then "
        f"docker exec \"$container\" su - \"$site\" -c {describe}; "
        f"docker exec \"$container\" su - \"$site\" -c {flush} || exit $?; "
        f"docker exec \"$container\" su - \"$site\" -c {collect}; rc=$?; "
        f"if [ \"$rc\" -eq 0 ]; then docker exec \"$container\" su - \"$site\" -c {describe}; fi; "
        "else "
        f"su - \"$site\" -c {describe}; "
        f"su - \"$site\" -c {flush} || exit $?; "
        f"su - \"$site\" -c {collect}; rc=$?; "
        f"if [ \"$rc\" -eq 0 ]; then su - \"$site\" -c {describe}; fi; "
        "fi; "
        "if [ \"$rc\" -eq 0 ]; then echo 'COLLECTION_STATUS=SUCCESS'; "
        "else echo \"COLLECTION_STATUS=FAILED RC=$rc\"; fi; exit \"$rc\""
    )


def collect_target_from_monitor(executor: Any, target_host: str) -> dict[str, Any]:
    """Executa a pós-coleta no MONITORING_HOST do mesmo envelope site-scoped."""

    host = _host(target_host)
    monitor_executor = getattr(executor, "parent", executor)
    command = build_post_correction_collection_command(host)
    try:
        result = monitor_executor.run_sudo(
            command,
            EnvironmentType.MONITORING,
            timeout=360,
        )
        stdout = redact_text(str(result.stdout or ""))
        stderr = redact_text(str(result.stderr or ""))
        ok = int(result.exit_code or 0) == 0 and "COLLECTION_STATUS=SUCCESS" in stdout
        return {
            "stage": "checkmk_post_correction_collection",
            "target_host": host,
            "status": "validated" if ok else "failed",
            "exit_code": int(result.exit_code or 0),
            "stdout": stdout,
            "stderr": stderr,
            "monitoring_context": True,
            "same_site_only": True,
        }
    except Exception as exc:
        return {
            "stage": "checkmk_post_correction_collection",
            "target_host": host,
            "status": "failed",
            "exit_code": 255,
            "stdout": "",
            "stderr": redact_text(f"{type(exc).__name__}: {exc}"),
            "monitoring_context": True,
            "same_site_only": True,
        }
