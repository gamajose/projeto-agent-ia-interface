from __future__ import annotations

import pytest

from app.services.vpn_menu import (
    normalize_client_name,
    parse_vpn_menu_entries,
    select_vpn_menu_entry,
)


def test_parse_normal_monitor_entry_and_display_name() -> None:
    output = """
N     IP_VPN          NOME_CLIENTE                                 PORTA           CLIENT
[1]   172.27.233.38   IBIAPABA (MONITOR)                           DEFAULT         CLIENT233038
Qual o Numero do Servidor Para Acesso (Q Para sair):
"""
    entry = select_vpn_menu_entry(output, "172.27.233.38")

    assert entry.index == 1
    assert entry.vpn_ip == "172.27.233.38"
    assert entry.raw_name == "IBIAPABA (MONITOR)"
    assert entry.display_name == "IBIAPABA MONITOR"
    assert entry.ssh_port == 22
    assert entry.client_code == "CLIENT233038"
    assert entry.is_pfsense is False


def test_parse_pfsense_entry_uses_first_numeric_port() -> None:
    output = r"""
N     IP_VPN          NOME_CLIENTE                                 PORTA           CLIENT
[1]   172.27.226.57   ATACADAO CENTRAL (PF)                        2224\3556\HTTP  CLIENT226057
Qual o Numero do Servidor Para Acesso (Q Para sair):
"""
    entry = select_vpn_menu_entry(output, "172.27.226.57")

    assert entry.display_name == "ATACADAO CENTRAL PF"
    assert entry.port_spec == r"2224\3556\HTTP"
    assert entry.ssh_port == 2224
    assert entry.is_pfsense is True


def test_parser_ignores_ansi_colors_and_selects_matching_ip() -> None:
    output = (
        "\x1b[33m[1]\x1b[0m   \x1b[35m172.27.232.109\x1b[0m  "
        "HOTBEL (MONITOR) DEFAULT CLIENT232109\r\n"
        "[2]   172.27.233.38  IBIAPABA (MONITOR) DEFAULT CLIENT233038\r\n"
    )
    entries = parse_vpn_menu_entries(output)
    selected = select_vpn_menu_entry(output, "172.27.232.109")

    assert len(entries) == 2
    assert selected.display_name == "HOTBEL MONITOR"


def test_missing_target_reports_ips_returned_by_menu() -> None:
    output = "[1] 172.27.232.109 HOTBEL (MONITOR) DEFAULT CLIENT232109"
    with pytest.raises(LookupError, match="172.27.232.109"):
        select_vpn_menu_entry(output, "172.27.200.10")


def test_normalize_client_name_removes_only_visual_parentheses() -> None:
    assert normalize_client_name("  HOTBEL   (MONITOR) ") == "HOTBEL MONITOR"
