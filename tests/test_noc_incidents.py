from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.noc_incidents import (
    _recent_transition_count,
    incident_fingerprint,
    incident_objective,
    normalize_checkmk_state,
)


def test_checkmk_states_are_normalized_for_incident_lifecycle() -> None:
    assert normalize_checkmk_state("OK") == {"raw": "OK", "kind": "ok", "severity": "healthy"}
    assert normalize_checkmk_state("0")["kind"] == "ok"
    assert normalize_checkmk_state("WARN")["severity"] == "attention"
    assert normalize_checkmk_state("CRIT")["severity"] == "critical"
    assert normalize_checkmk_state("DOWN")["kind"] == "problem"
    assert normalize_checkmk_state("UNKNOWN")["severity"] == "inconclusive"


def test_incident_fingerprint_is_stable_for_same_checkmk_service() -> None:
    left = incident_fingerprint(site="Cliente", host="SRV01", service="Check_MK Agent")
    right = incident_fingerprint(site="cliente", host="srv01", service="check_mk agent")
    other = incident_fingerprint(site="cliente", host="srv01", service="Filesystem /var")

    assert left == right
    assert left != other


def test_flapping_counts_only_normal_problem_transitions_inside_window() -> None:
    now = datetime.now(timezone.utc)
    events = [
        {"timestamp": (now - timedelta(minutes=20)).isoformat(), "kind": "problem"},
        {"timestamp": (now - timedelta(minutes=8)).isoformat(), "kind": "problem"},
        {"timestamp": (now - timedelta(minutes=7)).isoformat(), "kind": "ok"},
        {"timestamp": (now - timedelta(minutes=6)).isoformat(), "kind": "problem"},
        {"timestamp": (now - timedelta(minutes=5)).isoformat(), "kind": "problem"},
        {"timestamp": (now - timedelta(minutes=4)).isoformat(), "kind": "ok"},
        {"timestamp": (now - timedelta(minutes=3)).isoformat(), "kind": "problem"},
    ]

    assert _recent_transition_count(events, since=now - timedelta(minutes=10)) == 4


def test_incident_objective_tells_ai_about_flapping() -> None:
    objective = incident_objective(
        {
            "host": "srv-monitor",
            "service": "Check_MK Agent",
            "current_state": "CRIT",
            "site": "abc",
            "last_output": "Connection refused",
            "flapping": True,
            "recent_transition_count": 6,
        }
    )

    assert "Flapping detectado" in objective
    assert "6 transições" in objective
    assert "Check_MK Agent" in objective
    assert "evidências atuais" in objective
