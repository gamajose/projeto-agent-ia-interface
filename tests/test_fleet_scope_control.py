from __future__ import annotations

import pytest

from app.services.fleet_scope_control import parse_discovery_scope


def test_scope_wildcard_keeps_authorized_172_27_range() -> None:
    assert [str(item) for item in parse_discovery_scope("172.27.*")] == ["172.27.0.0/16"]
    assert [str(item) for item in parse_discovery_scope("172.27.*.*")] == ["172.27.0.0/16"]


def test_scope_three_octets_maps_one_24() -> None:
    assert [str(item) for item in parse_discovery_scope("172.27.1")] == ["172.27.1.0/24"]
    assert [str(item) for item in parse_discovery_scope("172.27.1.*")] == ["172.27.1.0/24"]


def test_scope_exact_ip_and_cidr_are_supported() -> None:
    assert [str(item) for item in parse_discovery_scope("172.27.1.50")] == ["172.27.1.50/32"]
    assert [str(item) for item in parse_discovery_scope("172.27.20.0/24")] == ["172.27.20.0/24"]


def test_scope_cannot_expand_outside_authorized_range() -> None:
    with pytest.raises(ValueError, match="fora do limite autorizado"):
        parse_discovery_scope("10.0.*")
