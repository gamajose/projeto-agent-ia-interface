from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _unique(values: list[Any], *, limit: int = 80) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _text_blob(evidence: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in evidence[-40:]:
        chunks.extend(
            (
                str(item.get("tool") or ""),
                str(item.get("command") or ""),
                str(item.get("stdout") or "")[-5000:],
                str(item.get("stderr") or "")[-2000:],
            )
        )
    return "\n".join(chunks)


def _infer_init_system(identity: dict[str, Any], runtime_context: dict[str, Any], blob: str) -> str:
    lowered = f"{identity} {runtime_context} {blob}".casefold()
    if "systemctl" in lowered or "systemd" in lowered or ".service" in lowered:
        return "systemd"
    if "xinetd" in lowered:
        return "xinetd"
    if "rc.d" in lowered or "service --status-all" in lowered or "/etc/init.d" in lowered:
        return "sysvinit"
    if "freebsd" in lowered or "pfsense" in lowered:
        return "rc.d"
    return "unknown"


def _infer_platform(identity: dict[str, Any], runtime_context: dict[str, Any], blob: str) -> dict[str, Any]:
    text = " ".join(
        (
            str(identity.get("os_name") or ""),
            str(identity.get("kernel") or ""),
            str(runtime_context.get("os_name") or ""),
            blob[:12000],
        )
    ).casefold()
    family = "unknown"
    if any(token in text for token in ("oracle linux", "red hat", "rhel", "centos", "rocky", "almalinux")):
        family = "rhel"
    elif any(token in text for token in ("ubuntu", "debian")):
        family = "debian"
    elif any(token in text for token in ("suse", "sles", "opensuse")):
        family = "suse"
    elif any(token in text for token in ("freebsd", "pfsense")):
        family = "freebsd"
    elif "linux" in text:
        family = "linux"

    version_match = re.search(
        r"(?:oracle linux|red hat enterprise linux|centos|rocky linux|almalinux|ubuntu|debian|sles|suse)[^0-9]{0,12}([0-9]+(?:\.[0-9]+){0,2})",
        text,
        flags=re.IGNORECASE,
    )
    return {
        "family": family,
        "name": str(identity.get("os_name") or runtime_context.get("os_name") or "unknown")[:240],
        "version": version_match.group(1) if version_match else None,
        "kernel": str(identity.get("kernel") or "")[:160] or None,
    }


def _infer_virtualization(identity: dict[str, Any], runtime_context: dict[str, Any], blob: str) -> str:
    text = f"{identity} {runtime_context} {blob}".casefold()
    mapping = (
        ("docker", ("/.dockerenv", "docker container", "container=docker")),
        ("podman", ("podman", "container=podman")),
        ("kubernetes", ("kubernetes", "kubepods", "kubelet")),
        ("vmware", ("vmware", "esxi")),
        ("kvm", ("kvm", "qemu")),
        ("hyper-v", ("hyper-v", "microsoft corporation virtual machine")),
        ("virtualbox", ("virtualbox", "oracle corporation virtualbox")),
        ("physical", ("chassis type: rack", "product name: poweredge", "product name: proliant")),
    )
    for label, tokens in mapping:
        if any(token in text for token in tokens):
            return label
    return "unknown"


def _infer_monitoring_stack(profile: str | None, runtime_context: dict[str, Any], blob: str) -> list[str]:
    text = f"{profile or ''} {runtime_context} {blob}".casefold()
    stack: list[str] = []
    mapping = (
        ("checkmk", ("checkmk", "check_mk", "omd", "cmk ")),
        ("zabbix", ("zabbix",)),
        ("prometheus", ("prometheus", "node_exporter")),
        ("grafana", ("grafana",)),
        ("snmp", ("snmpd", "bsnmpd", "snmpget", "snmpwalk")),
        ("openvpn", ("openvpn",)),
        ("ipsec", ("strongswan", "charon", "ipsec")),
    )
    for name, tokens in mapping:
        if any(token in text for token in tokens):
            stack.append(name)
    return stack


def _extract_omd_sites(runtime_context: dict[str, Any], blob: str) -> list[str]:
    values: list[str] = []
    for service in runtime_context.get("services") or []:
        match = re.search(r"(?:omd|site)[-_:@ ]([a-z0-9_-]{2,32})", str(service), re.IGNORECASE)
        if match:
            values.append(match.group(1))
    for match in re.finditer(r"/omd/sites/([a-z0-9_-]{2,32})", blob, re.IGNORECASE):
        values.append(match.group(1))
    for match in re.finditer(r"\b([a-z0-9_-]{2,24})\s+(?:2\.[0-9].*?)?\s*(?:running|stopped|disabled|partially)", blob, re.IGNORECASE):
        candidate = match.group(1)
        if candidate not in {"site", "overall", "state", "version"}:
            values.append(candidate)
    return _unique(values, limit=30)


def build_environment_fingerprint(
    *,
    identity: dict[str, Any] | None,
    runtime_context: dict[str, Any] | None,
    evidence: list[dict[str, Any]] | None,
    profile: str | None,
    environment: dict[str, Any] | str | None,
) -> dict[str, Any]:
    """Cria um fingerprint estável e sem credenciais do ambiente investigado."""
    identity = dict(identity or {})
    runtime_context = dict(runtime_context or {})
    evidence = [item for item in (evidence or []) if isinstance(item, dict)]
    blob = _text_blob(evidence)

    binaries = _unique(list(runtime_context.get("binaries") or []), limit=120)
    services = _unique(list(runtime_context.get("services") or []), limit=120)
    listeners = _unique(list(runtime_context.get("listeners") or []), limit=120)
    containers = _unique(list(runtime_context.get("containers") or []), limit=80)
    platform = _infer_platform(identity, runtime_context, blob)
    init_system = _infer_init_system(identity, runtime_context, blob)
    virtualization = _infer_virtualization(identity, runtime_context, blob)
    monitoring_stack = _infer_monitoring_stack(profile, runtime_context, blob)
    omd_sites = _extract_omd_sites(runtime_context, blob)

    stable = {
        "platform": platform,
        "init_system": init_system,
        "virtualization": virtualization,
        "profile": profile or "unknown",
        "monitoring_stack": monitoring_stack,
        "binaries": sorted(binaries),
        "service_names": sorted(services),
        "listener_signatures": sorted(listeners),
        "container_names": sorted(containers),
        "omd_sites": sorted(omd_sites),
    }
    signature = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:20]

    if isinstance(environment, dict):
        environment_name = str(environment.get("environment") or environment.get("requested") or "unknown")
        environment_confidence = int(environment.get("confidence") or 0)
    else:
        environment_name = str(environment or "unknown")
        environment_confidence = 0

    return {
        "version": 1,
        "signature": signature,
        "hostname": str(identity.get("hostname") or "")[:255] or None,
        "platform": platform,
        "init_system": init_system,
        "virtualization": virtualization,
        "profile": profile or "unknown",
        "environment": environment_name,
        "environment_confidence": environment_confidence,
        "monitoring_stack": monitoring_stack,
        "omd_sites": omd_sites,
        "capabilities": {
            "binaries": binaries,
            "services": services,
            "listeners": listeners,
            "containers": containers,
        },
        "discovery_status": runtime_context.get("discovery_status") or "unknown",
    }
