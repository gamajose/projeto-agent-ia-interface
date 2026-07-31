from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_ENTRY_PREFIX = re.compile(r"^\s*\[(?P<index>\d+)]\s+(?P<vpn_ip>\S+)\s+(?P<tail>.+?)\s*$")
_PORT_NUMBER = re.compile(r"(?<!\d)(?P<port>\d{2,5})(?!\d)")


@dataclass(frozen=True)
class VPNMenuEntry:
    index: int
    vpn_ip: str
    raw_name: str
    display_name: str
    port_spec: str
    client_code: str
    ssh_port: int
    is_pfsense: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_terminal_codes(value: str) -> str:
    """Remove ANSI e normaliza retornos de terminal sem apagar o conteúdo."""
    return _ANSI_ESCAPE.sub("", str(value or "")).replace("\r", "")


def normalize_client_name(value: str) -> str:
    """Converte ``HOTBEL (MONITOR)`` em ``HOTBEL MONITOR`` para a interface."""
    text = re.sub(r"\(([^)]+)\)", r" \1 ", str(value or ""))
    return " ".join(text.split()).strip()


def _port_from_spec(port_spec: str, default_port: int) -> int:
    if str(port_spec or "").strip().casefold() == "default":
        return int(default_port)
    match = _PORT_NUMBER.search(str(port_spec or ""))
    if not match:
        return int(default_port)
    port = int(match.group("port"))
    return port if 1 <= port <= 65535 else int(default_port)


def parse_vpn_menu_entries(
    output: str,
    *,
    default_port: int = 22,
    pfsense_port: int = 2224,
) -> list[VPNMenuEntry]:
    """Interpreta as linhas exibidas pelo comando ``vpn IP``.

    O inventário usa nome de cliente com espaços e mantém as duas últimas
    colunas sem espaços: ``PORTA`` e ``CLIENT``. Por isso a leitura é feita da
    direita para a esquerda e não depende do alinhamento visual do terminal.
    """
    entries: list[VPNMenuEntry] = []
    for raw_line in strip_terminal_codes(output).splitlines():
        match = _ENTRY_PREFIX.match(raw_line)
        if not match:
            continue
        tokens = match.group("tail").split()
        if len(tokens) < 3:
            continue
        client_code = tokens[-1]
        port_spec = tokens[-2]
        raw_name = " ".join(tokens[:-2]).strip()
        if not raw_name:
            continue
        ssh_port = _port_from_spec(port_spec, default_port)
        upper_name = raw_name.upper()
        is_pfsense = (
            "(PF)" in upper_name
            or "PFSENSE" in upper_name
            or ssh_port == int(pfsense_port)
        )
        entries.append(
            VPNMenuEntry(
                index=int(match.group("index")),
                vpn_ip=match.group("vpn_ip").strip(),
                raw_name=raw_name,
                display_name=normalize_client_name(raw_name),
                port_spec=port_spec,
                client_code=client_code,
                ssh_port=ssh_port,
                is_pfsense=is_pfsense,
            )
        )
    return entries


def select_vpn_menu_entry(
    output: str,
    target_host: str,
    *,
    default_port: int = 22,
    pfsense_port: int = 2224,
) -> VPNMenuEntry:
    entries = parse_vpn_menu_entries(
        output,
        default_port=default_port,
        pfsense_port=pfsense_port,
    )
    target = str(target_host or "").strip()
    for entry in entries:
        if entry.vpn_ip == target:
            return entry
    available = ", ".join(entry.vpn_ip for entry in entries[:10]) or "nenhum"
    raise LookupError(
        f"o IP {target!r} não apareceu no inventário retornado pelo comando vpn; "
        f"IPs encontrados: {available}"
    )
