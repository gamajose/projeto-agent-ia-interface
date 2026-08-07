from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.settings import Settings, get_settings


_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$")


class AccessMonitorError(ValueError):
    pass


@dataclass(frozen=True)
class AccessMonitor:
    id: str
    label: str
    host: str
    builtin: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "host": self.host,
            "display": f"{self.label} — {self.host}",
            "builtin": self.builtin,
        }


def _valid_host(value: str) -> str:
    host = str(value or "").strip()
    if not host:
        raise AccessMonitorError("informe o IP ou hostname do servidor de acesso")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if not _HOSTNAME.fullmatch(host):
        raise AccessMonitorError("IP/hostname do servidor de acesso é inválido")
    return host


def _registry_path(settings: Settings) -> Path:
    return Path(settings.access_monitor_registry_path).expanduser()


def _builtin_monitors(settings: Settings) -> list[AccessMonitor]:
    rows = [
        ("monitor1", "Monitor 1", settings.ssh_bastion_host),
        ("monitor2", "Monitor 2", settings.ssh_nuvem),
        ("monitor5", "Monitor 5", settings.ssh_cmk05),
    ]
    output: list[AccessMonitor] = []
    for monitor_id, label, host in rows:
        value = str(host or "").strip()
        if value:
            output.append(AccessMonitor(monitor_id, label, _valid_host(value), True))
    return output


def _custom_rows(settings: Settings) -> list[AccessMonitor]:
    path = _registry_path(settings)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = payload.get("monitors", []) if isinstance(payload, dict) else []
    output: list[AccessMonitor] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        monitor_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "Servidor de acesso").strip()[:80]
        try:
            host = _valid_host(str(item.get("host") or ""))
        except AccessMonitorError:
            continue
        if monitor_id and not monitor_id.startswith("monitor"):
            output.append(AccessMonitor(monitor_id, label or host, host, False))
    return output


def list_access_monitors(settings: Settings | None = None) -> list[AccessMonitor]:
    settings = settings or get_settings()
    rows = [*_builtin_monitors(settings), *_custom_rows(settings)]
    seen: set[str] = set()
    output: list[AccessMonitor] = []
    for item in rows:
        if item.id in seen:
            continue
        seen.add(item.id)
        output.append(item)
    return output


def resolve_access_monitor(monitor_id: str | None, settings: Settings | None = None) -> AccessMonitor:
    settings = settings or get_settings()
    requested = str(monitor_id or "monitor1").strip() or "monitor1"
    rows = list_access_monitors(settings)
    match = next((item for item in rows if item.id == requested), None)
    if match:
        return match
    if requested == "monitor1" and settings.ssh_bastion_host:
        return AccessMonitor("monitor1", "Monitor 1", _valid_host(settings.ssh_bastion_host), True)
    raise AccessMonitorError(f"servidor de acesso não cadastrado: {requested}")


def settings_for_access_monitor(monitor_id: str | None, settings: Settings | None = None) -> Settings:
    settings = settings or get_settings()
    monitor = resolve_access_monitor(monitor_id, settings)
    # Só o host muda. Porta, usuário, senha, chave e known_hosts continuam sendo
    # exatamente a credencial operacional SSH_SRV_VPN_* já usada no Monitor 1.
    return settings.model_copy(update={"ssh_bastion_host": monitor.host})


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def add_access_monitor(label: str, host: str, settings: Settings | None = None) -> AccessMonitor:
    settings = settings or get_settings()
    normalized_host = _valid_host(host)
    normalized_label = " ".join(str(label or "").split())[:80] or f"Servidor {normalized_host}"
    existing = list_access_monitors(settings)
    duplicate = next((item for item in existing if item.host == normalized_host), None)
    if duplicate:
        return duplicate

    path = _registry_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    rows = payload.get("monitors", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    monitor = AccessMonitor(f"custom-{uuid4().hex[:10]}", normalized_label, normalized_host, False)
    rows.append({"id": monitor.id, "label": monitor.label, "host": monitor.host})
    _atomic_write(path, {"monitors": rows})
    return monitor
