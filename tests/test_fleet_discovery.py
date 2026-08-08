from __future__ import annotations

import ipaddress

from app.services.fleet_discovery import _addresses_for_batch, classify_fingerprint


def test_monitoring_on_production_host_keeps_production_environment() -> None:
    result = classify_fingerprint(
        {
            "client_name": "CLIENTE PROD",
            "hostname": "srv-app-01",
            "omd_dir": "yes",
            "omd_sites": "SITE VERSION\ncli 2.5.0p9",
            "cmk_version": "Checkmk 2.5.0p9",
            "checkmk_processes": "cmc /omd/sites/cli/bin/cmc",
        }
    )
    assert result["monitoring_detected"] is True
    assert result["environment"] == "production"
    assert "monitoring" in result["roles"]
    assert "production" in result["roles"]


def test_monitoring_capability_without_strong_label_stays_unknown_for_changes() -> None:
    result = classify_fingerprint(
        {
            "client_name": "CLIENTE ABC",
            "hostname": "srv-01",
            "omd_dir": "yes",
            "omd_dirs": "abc",
            "checkmk_containers": "checkmk-abc-25 local/checkmk-ol8:2.5.0p9",
        }
    )
    assert result["monitoring_detected"] is True
    assert result["environment"] == "unknown"
    assert result["environment_confidence"] == 0
    assert "monitoring" in result["roles"]


def test_dedicated_monitor_is_classified_as_monitoring() -> None:
    result = classify_fingerprint(
        {
            "client_name": "HOTBEL MONITOR",
            "hostname": "2com-monitor",
            "omd_dir": "yes",
            "omd_sites": "SITE VERSION\nhot 2.5.0p9",
        }
    )
    assert result["monitoring_detected"] is True
    assert result["environment"] == "monitoring"
    assert result["checkmk_sites"] == ["hot"]


def test_full_cidr_cursor_advances_without_restarting_network() -> None:
    networks = [ipaddress.ip_network("172.27.10.0/30"), ipaddress.ip_network("172.27.20.1/32")]
    first, cidr, offset, finished = _addresses_for_batch(
        networks,
        cursor_cidr=0,
        cursor_offset=0,
        batch_size=2,
    )
    assert first == ["172.27.10.1", "172.27.10.2"]
    assert cidr == 1
    assert offset == 0
    assert finished is False

    second, cidr, offset, finished = _addresses_for_batch(
        networks,
        cursor_cidr=cidr,
        cursor_offset=offset,
        batch_size=2,
    )
    assert second == ["172.27.20.1"]
    assert cidr == 2
    assert offset == 0
    assert finished is True
