from __future__ import annotations

import pytest

from app.services.ensemble_instrumentation import _correction_consensus, get_ensemble_config
from app.services.range_investigation import (
    RangeInvestigationError,
    _expand_range,
    _fallback_synthesis,
    _scan_command,
    looks_like_ip_range,
)
from app.web import InvestigationPayload


def test_range_parser_accepts_cidr_and_short_ipv4_interval() -> None:
    assert looks_like_ip_range("172.27.232.0/30")
    assert _expand_range("172.27.232.0/30", max_addresses=32, private_only=True) == [
        "172.27.232.1",
        "172.27.232.2",
    ]
    assert _expand_range("172.27.232.10-12", max_addresses=32, private_only=True) == [
        "172.27.232.10",
        "172.27.232.11",
        "172.27.232.12",
    ]


def test_range_parser_blocks_public_network_by_default() -> None:
    with pytest.raises(RangeInvestigationError):
        _expand_range("8.8.8.0/30", max_addresses=32, private_only=True)


def test_range_parser_enforces_bound_before_scanning() -> None:
    with pytest.raises(RangeInvestigationError):
        _expand_range("172.27.232.0/24", max_addresses=64, private_only=True)


def test_scan_command_contains_only_validated_hosts_ports_and_concurrency() -> None:
    command = _scan_command(["172.27.232.10", "172.27.232.11"], [22, 2224], 16)
    assert "172.27.232.10" in command
    assert "172.27.232.11" in command
    assert "for p in 22 2224" in command
    assert "c % 16" in command


def test_correction_ensemble_requires_matching_votes() -> None:
    outputs = [
        {
            "actions": [
                {"tool": "systemd.recover_unit", "arguments": {"unit": "check-mk-agent.socket", "action": "restart"}},
                {"tool": "systemd.recover_unit", "arguments": {"unit": "chronyd.service", "action": "restart"}},
            ]
        },
        {
            "actions": [
                {"tool": "systemd.recover_unit", "arguments": {"unit": "check-mk-agent.socket", "action": "restart"}},
            ]
        },
        {"actions": []},
    ]
    consensus = _correction_consensus(outputs, minimum_votes=2)
    assert len(consensus["actions"]) == 1
    assert consensus["actions"][0]["arguments"]["unit"] == "check-mk-agent.socket"
    assert consensus["actions"][0]["ensemble_votes"] == 2


def test_range_synthesis_fallback_prioritizes_critical_host() -> None:
    result = _fallback_synthesis(
        [
            {"address": "172.27.232.10", "status": "healthy", "confidence": 90, "facts": []},
            {
                "address": "172.27.232.11",
                "status": "critical",
                "confidence": 75,
                "probable_cause": "filesystem esgotado",
                "conclusion": "serviço impactado pelo disco",
                "facts": ["/ em 100%"],
                "recommendations": ["liberar espaço com procedimento aprovado"],
            },
        ]
    )
    assert result["root_host"] == "172.27.232.11"
    assert result["status"] == "critical"


def test_web_payload_allows_general_analysis_without_objective() -> None:
    payload = InvestigationPayload(target="172.27.232.0/24")
    assert payload.objective == ""


def test_ensemble_defaults_are_enabled(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_ENSEMBLE_ENABLED", raising=False)
    monkeypatch.delenv("AGENT_ENSEMBLE_SIZE", raising=False)
    config = get_ensemble_config()
    assert config.enabled is True
    assert config.size >= 2
