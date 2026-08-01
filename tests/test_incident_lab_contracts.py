from __future__ import annotations

from pathlib import Path

import yaml


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "labs" / "scenarios"


def test_incident_scenarios_declare_valid_expectations() -> None:
    files = sorted(SCENARIO_DIR.glob("*.yml"))
    assert files
    checked = 0
    for path in files:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        expected = payload.get("expected")
        if not expected:
            continue
        checked += 1
        assert isinstance(expected, dict), path
        assert expected.get("primary_alert"), path
        assert expected.get("probable_cause"), path
        quality = int(expected.get("minimum_quality") or 0)
        assert 1 <= quality <= 100, path
        prohibited = expected.get("prohibited_actions") or []
        assert isinstance(prohibited, list), path
        assert all(isinstance(item, str) and item.strip() for item in prohibited), path
    assert checked >= 4


def test_new_incident_scenarios_cover_operational_failures() -> None:
    required = {
        "checkmk-automation-helper-stopped.yml",
        "checkmk-omd-partial.yml",
        "checkmk-agent-6556-refused.yml",
        "network-snmp-timeout.yml",
    }
    assert required.issubset({path.name for path in SCENARIO_DIR.glob("*.yml")})
