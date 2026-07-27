from __future__ import annotations

import ipaddress
import os

os.environ.setdefault("POSTGRES_DSN", "sqlite+pysqlite:///:memory:")

from app.web import _is_allowed_client


def test_allows_client_inside_configured_network() -> None:
    networks = (ipaddress.ip_network("172.27.232.0/24"),)
    assert _is_allowed_client("172.27.232.203", networks)


def test_blocks_client_outside_configured_network() -> None:
    networks = (ipaddress.ip_network("172.27.232.0/24"),)
    assert not _is_allowed_client("10.45.1.24", networks)


def test_blocks_invalid_client_address() -> None:
    networks = (ipaddress.ip_network("127.0.0.1/32"),)
    assert not _is_allowed_client("host.example", networks)
