from __future__ import annotations

import re
import time
from typing import Any

from app.core.policies import EnvironmentType
from app.services.progress import report_progress
from app.services.ssh import SSHExecutor


_TRIAGE_COMMAND = r"""
printf 'HOSTNAME='; hostname 2>/dev/null || true
printf 'UPTIME='; uptime 2>/dev/null || true
printf 'KERNEL='; uname -r 2>/dev/null || true
printf 'LOAD='; cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || true
printf 'MEMORY='; free -m 2>/dev/null | awk '/^Mem:/ {print $3 "/" $2} /^Swap:/ {print " swap=" $3 "/" $2}' | tr '\n' ' '; echo
printf 'FAILED_UNITS='; systemctl --failed --no-legend --plain 2>/dev/null | wc -l || echo 0
printf 'FILESYSTEMS_BEGIN\n'; df -P -x tmpfs -x devtmpfs 2>/dev/null | awk 'NR>1 {print $5 "|" $6}'; printf 'FILESYSTEMS_END\n'
printf 'UNHEALTHY_BEGIN\n'; docker ps -a --filter health=unhealthy --format '{{.Names}}|{{.Status}}' 2>/dev/null || true; printf 'UNHEALTHY_END\n'
printf 'OMD_BEGIN\n'; omd sites --bare 2>/dev/null || true; printf 'OMD_END\n'
""".strip()


def _section(output: str, name: str) -> list[str]:
    match = re.search(
        rf"{re.escape(name)}_BEGIN\n(?P<body>.*?){re.escape(name)}_END",
        output,
        flags=re.S,
    )
    if not match:
        return []
    return [line.strip() for line in match.group("body").splitlines() if line.strip()]


def _value(output: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}=(.*)$", output, flags=re.M)
    return match.group(1).strip() if match else ""


def _percent(value: str) -> int:
    try:
        return int(value.strip().rstrip("%"))
    except ValueError:
        return 0


def triage_host(
    executor: SSHExecutor,
    *,
    objective: str,
    environment: EnvironmentType,
    label: str,
    timeout: int,
) -> dict[str, Any]:
    """Executa uma única coleta curta e determinística antes do deep dive."""
    started = time.monotonic()
    report_progress(
        "multi_host_triage",
        detail=f"Triagem rápida em {label}.",
        host=executor.host,
        percent=44,
    )
    result = executor.run(_TRIAGE_COMMAND, environment, approved=False, timeout=timeout)
    output = result.stdout or ""
    failed_units = int(_value(output, "FAILED_UNITS") or 0)
    filesystems = _section(output, "FILESYSTEMS")
    unhealthy = _section(output, "UNHEALTHY")
    omd_sites = _section(output, "OMD")
    objective_text = str(objective or "").casefold()

    score = 0
    reasons: list[str] = []
    critical_filesystems = []
    warning_filesystems = []
    for row in filesystems:
        usage, _, mount = row.partition("|")
        percentage = _percent(usage)
        if percentage >= 90:
            critical_filesystems.append(f"{mount} {percentage}%")
        elif percentage >= 80:
            warning_filesystems.append(f"{mount} {percentage}%")
    if failed_units:
        score += min(40, 15 + failed_units * 5)
        reasons.append(f"{failed_units} unidade(s) systemd com falha")
    if unhealthy:
        score += min(45, 20 + len(unhealthy) * 8)
        reasons.append(f"{len(unhealthy)} container(s) unhealthy")
    if critical_filesystems:
        score += 35
        reasons.append("filesystem crítico: " + ", ".join(critical_filesystems[:3]))
    elif warning_filesystems:
        score += 15
        reasons.append("filesystem em atenção: " + ", ".join(warning_filesystems[:3]))
    if omd_sites and any(term in objective_text for term in ("checkmk", "omd", "monitor", "sensor", "alerta")):
        score += 18
        reasons.append(f"{len(omd_sites)} site(s) OMD relacionado(s) ao objetivo")
    role_hint = str(getattr(executor, "route", {}).get("role") or "").casefold()
    if role_hint == "monitoring" and any(term in objective_text for term in ("checkmk", "monitor", "sensor")):
        score += 20
        reasons.append("host de monitoramento relacionado ao alerta")

    status = "attention" if score >= 20 else "healthy"
    confidence = min(90, 55 + min(score, 35))
    summary = (
        "; ".join(reasons)
        if reasons
        else "A triagem não encontrou falhas básicas que justifiquem aprofundamento imediato."
    )
    evidence = {
        "tool": "multi_host.quick_triage",
        "status": "executed" if result.exit_code == 0 else "failed",
        "exit_code": result.exit_code,
        "stdout": output,
        "stderr": result.stderr,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "host": executor.host,
    }
    return {
        "triage_only": True,
        "address": executor.host,
        "hostname": _value(output, "HOSTNAME") or None,
        "status": status,
        "confidence": confidence,
        "score": min(100, score),
        "summary": summary,
        "probable_cause": reasons[0] if reasons else "Nenhuma causa local evidente na triagem.",
        "facts": reasons or ["Triagem básica concluída sem falhas evidentes."],
        "recommendations": (
            ["Aprofundar este host para confirmar a causa."]
            if score >= 20
            else ["Manter como evidência comparativa; aprofundar apenas se outro achado apontar dependência."]
        ),
        "triage": {
            "failed_units": failed_units,
            "filesystems": filesystems,
            "unhealthy_containers": unhealthy,
            "omd_sites": omd_sites,
            "score": min(100, score),
            "duration_ms": evidence["duration_ms"],
        },
        "evidence": [evidence],
    }
