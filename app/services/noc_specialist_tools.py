from __future__ import annotations

import shlex
import time
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.redaction import redact_object, redact_text
from app.services.secrets import get_secret
from app.services.ssh import SSHExecutor


_SPECIALIST_DESCRIPTORS: tuple[dict[str, Any], ...] = (
    {
        "name": "snmp.transport",
        "category": "snmp",
        "description": "Valida rota, alcance ICMP e UDP/161 até um equipamento SNMP sem alterar o destino.",
        "arguments": {"host": "IP/hostname"},
        "requires_any": ["ip", "ping", "nc"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "snmp.v2.system",
        "category": "snmp",
        "description": "Consulta sysDescr, sysName e sysUpTime via SNMP v2c usando a community protegida do Agent.",
        "arguments": {"host": "IP/hostname"},
        "requires_any": ["snmpget", "snmpwalk"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "snmp.v3.system",
        "category": "snmp",
        "description": "Consulta OIDs básicos via SNMPv3 usando credenciais protegidas do Agent.",
        "arguments": {"host": "IP/hostname"},
        "requires_any": ["snmpget", "snmpwalk"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "snmp.auto.system",
        "category": "snmp",
        "description": "Tenta SNMPv3 e v2c conforme as credenciais disponíveis e informa qual caminho respondeu.",
        "arguments": {"host": "IP/hostname"},
        "requires_any": ["snmpget", "snmpwalk"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "bmc.detect.local",
        "category": "hardware",
        "description": "Identifica fabricante/modelo, BMC/IPMI e endereço de gerenciamento visível a partir do sistema operacional.",
        "arguments": {},
        "requires_any": ["dmidecode", "ipmitool"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "bmc.ipmi.sensors",
        "category": "hardware",
        "description": "Lê sensores IPMI locais de temperatura, energia, fans, tensão e presença sem alterar hardware.",
        "arguments": {},
        "requires_any": ["ipmitool"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "bmc.ipmi.sel",
        "category": "hardware",
        "description": "Lê o System Event Log IPMI recente para correlacionar falhas físicas.",
        "arguments": {"lines": "10-300"},
        "requires_any": ["ipmitool"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
    {
        "name": "bmc.ipmi.fru",
        "category": "hardware",
        "description": "Lê inventário FRU via IPMI sem modificar configuração do equipamento.",
        "arguments": {},
        "requires_any": ["ipmitool"],
        "transport": "ssh",
        "correction": False,
        "adaptive": True,
        "operational": True,
    },
)

_NAMES = {str(item["name"]) for item in _SPECIALIST_DESCRIPTORS}


def describe_noc_specialist_tools() -> list[dict[str, Any]]:
    return [dict(item) for item in _SPECIALIST_DESCRIPTORS]


def is_noc_specialist_tool(name: str) -> bool:
    return name in _NAMES


def _safe_host(value: Any) -> str:
    host = str(value or "").strip()
    if not host or len(host) > 255 or host.startswith("-"):
        raise ValueError("host SNMP inválido")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(char not in allowed for char in host):
        raise ValueError("host SNMP inválido")
    return host


def _bounded_lines(value: Any, default: int = 100) -> int:
    try:
        lines = int(value or default)
    except (TypeError, ValueError) as exc:
        raise ValueError("quantidade de linhas inválida") from exc
    return max(10, min(lines, 300))


def _snmp_v2_command(host: str, settings: Settings) -> str:
    community = get_secret("SNMP_V2_COMMUNITY", settings.snmp_v2_community, settings=settings, required=True)
    return (
        "if command -v snmpget >/dev/null 2>&1; then "
        f"snmpget -v2c -c {shlex.quote(community)} -t 3 -r 1 {shlex.quote(host)} "
        "1.3.6.1.2.1.1.1.0 1.3.6.1.2.1.1.3.0 1.3.6.1.2.1.1.5.0; "
        "else "
        f"snmpwalk -v2c -c {shlex.quote(community)} -t 3 -r 1 {shlex.quote(host)} 1.3.6.1.2.1.1 2>&1 | head -n 20; fi"
    )


def _snmp_v3_command(host: str, settings: Settings) -> str:
    user = get_secret("SNMP_V3_USER", settings.snmp_v3_user, settings=settings, required=True)
    auth_password = get_secret(
        "SNMP_V3_AUTH_PASSWORD",
        settings.snmp_v3_auth_password,
        settings=settings,
        required=True,
    )
    auth_protocol = str(settings.snmp_v3_auth_protocol or "SHA").upper()
    if auth_protocol not in {"SHA", "MD5", "SHA-224", "SHA-256", "SHA-384", "SHA-512"}:
        auth_protocol = "SHA"
    priv_password = get_secret("SNMP_V3_PRIV_PASSWORD", settings.snmp_v3_priv_password, settings=settings)
    priv_protocol = str(settings.snmp_v3_priv_protocol or "AES").upper()
    if priv_protocol not in {"AES", "AES128", "DES"}:
        priv_protocol = "AES"
    if priv_password:
        security = (
            f"-l authPriv -u {shlex.quote(user)} -a {auth_protocol} -A {shlex.quote(auth_password)} "
            f"-x {priv_protocol} -X {shlex.quote(priv_password)}"
        )
    else:
        security = f"-l authNoPriv -u {shlex.quote(user)} -a {auth_protocol} -A {shlex.quote(auth_password)}"
    return (
        "if command -v snmpget >/dev/null 2>&1; then "
        f"snmpget -v3 {security} -t 3 -r 1 {shlex.quote(host)} "
        "1.3.6.1.2.1.1.1.0 1.3.6.1.2.1.1.3.0 1.3.6.1.2.1.1.5.0; "
        "else "
        f"snmpwalk -v3 {security} -t 3 -r 1 {shlex.quote(host)} 1.3.6.1.2.1.1 2>&1 | head -n 20; fi"
    )


def _execute(executor: SSHExecutor, environment: EnvironmentType, command: str, *, sudo: bool = False, timeout: int = 45) -> dict[str, Any]:
    result = executor.run_sudo(command, environment, timeout=timeout) if sudo else executor.run(command, environment, timeout=timeout)
    stdout = redact_text(result.stdout)
    stderr = redact_text(result.stderr)
    return {
        "status": "executed" if result.exit_code == 0 else "failed",
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "normalized": {
            "line_count": len([line for line in stdout.splitlines() if line.strip()]),
            "responded": result.exit_code == 0 and bool(stdout.strip()),
            "sample": [line.strip() for line in stdout.splitlines() if line.strip()][-40:],
        },
    }


def execute_noc_specialist_tool(
    executor: SSHExecutor,
    environment: EnvironmentType,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    args = dict(arguments or {})
    base = {
        "tool": name,
        "arguments": redact_object(args),
        "correction": False,
        "adaptive": True,
        "operational": True,
    }
    started = time.monotonic()
    try:
        if name == "snmp.transport":
            host = _safe_host(args.get("host"))
            command = (
                f"ip route get {shlex.quote(host)} 2>&1 || true; "
                f"ping -c 2 -W 2 {shlex.quote(host)} 2>&1 || true; "
                f"if command -v nc >/dev/null 2>&1; then timeout 6 nc -zvu {shlex.quote(host)} 161 2>&1 || true; fi"
            )
            result = _execute(executor, environment, command, timeout=20)
        elif name == "snmp.v2.system":
            host = _safe_host(args.get("host"))
            result = _execute(executor, environment, _snmp_v2_command(host, settings), timeout=30)
        elif name == "snmp.v3.system":
            host = _safe_host(args.get("host"))
            result = _execute(executor, environment, _snmp_v3_command(host, settings), timeout=30)
        elif name == "snmp.auto.system":
            host = _safe_host(args.get("host"))
            commands: list[tuple[str, str]] = []
            try:
                commands.append(("v3", _snmp_v3_command(host, settings)))
            except Exception:
                pass
            try:
                commands.append(("v2c", _snmp_v2_command(host, settings)))
            except Exception:
                pass
            if not commands:
                raise ValueError("nenhuma credencial SNMP v2c/v3 está configurada no Agent")
            attempts: list[dict[str, Any]] = []
            selected: dict[str, Any] | None = None
            for version, command in commands:
                attempt = _execute(executor, environment, command, timeout=30)
                attempt["version"] = version
                attempts.append(attempt)
                if attempt.get("exit_code") == 0 and attempt.get("stdout"):
                    selected = attempt
                    break
            selected = selected or attempts[-1]
            result = {
                **selected,
                "normalized": {
                    **dict(selected.get("normalized") or {}),
                    "selected_version": selected.get("version"),
                    "attempts": [
                        {"version": item.get("version"), "exit_code": item.get("exit_code"), "responded": bool(item.get("stdout"))}
                        for item in attempts
                    ],
                },
            }
        elif name == "bmc.detect.local":
            command = (
                "printf '%s\\n' '=== DMI ==='; "
                "dmidecode -t 1 2>/dev/null | grep -E 'Manufacturer:|Product Name:|Serial Number:|UUID:' || true; "
                "printf '%s\\n' '=== IPMI MC ==='; ipmitool mc info 2>&1 || true; "
                "printf '%s\\n' '=== IPMI LAN ==='; ipmitool lan print 2>&1 | grep -E 'IP Address|MAC Address|Default Gateway|IP Address Source' || true"
            )
            result = _execute(executor, environment, command, sudo=True, timeout=45)
        elif name == "bmc.ipmi.sensors":
            result = _execute(executor, environment, "ipmitool sensor list 2>&1", sudo=True, timeout=60)
        elif name == "bmc.ipmi.sel":
            lines = _bounded_lines(args.get("lines"), 120)
            result = _execute(
                executor,
                environment,
                f"ipmitool sel elist 2>&1 | tail -n {lines}",
                sudo=True,
                timeout=60,
            )
        elif name == "bmc.ipmi.fru":
            result = _execute(executor, environment, "ipmitool fru 2>&1", sudo=True, timeout=60)
        else:
            return {**base, "status": "blocked", "reason": "ferramenta especialista desconhecida", "exit_code": 255, "stdout": "", "stderr": "", "normalized": {}}
        return {
            **base,
            **result,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            **base,
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "exit_code": 255,
            "stdout": "",
            "stderr": redact_text(str(exc)),
            "normalized": {},
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
