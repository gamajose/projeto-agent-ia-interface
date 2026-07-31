from __future__ import annotations

from types import SimpleNamespace

from app.core.settings import Settings
from app.services import inventory_learning


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_dsn="sqlite+pysqlite:///:memory:",
        ssh_default_port=22,
    )


def test_extract_host_port_supports_ipv4_and_explicit_port() -> None:
    assert inventory_learning._extract_host_port("192.168.28.10", 22) == ("192.168.28.10", 22)
    assert inventory_learning._extract_host_port("192.168.28.10:2222", 22) == ("192.168.28.10", 2222)
    assert inventory_learning._extract_host_port("[2001:db8::10]:2200", 22) == ("2001:db8::10", 2200)
    assert inventory_learning._extract_host_port("servidor-local", 22) == (None, 22)


def test_internal_ips_extracts_real_addresses_without_interface_text() -> None:
    result = inventory_learning._internal_ips(
        {
            "ip_brief": "lo UNKNOWN 127.0.0.1/8\neth0 UP 10.0.0.10/24\ntun0 UP 172.27.232.203/24",
        }
    )
    assert result == ["127.0.0.1", "10.0.0.10", "172.27.232.203"]


def test_learn_result_inventory_records_resolved_host(monkeypatch) -> None:
    captured = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="host-1",
            vpn_ip=kwargs["vpn_ip"],
            ssh_port=kwargs["ssh_port"],
            hostname=kwargs["hostname"],
            environment=kwargs["environment"],
        )

    monkeypatch.setattr(inventory_learning, "ensure_database_schema", lambda: [])
    monkeypatch.setattr(inventory_learning, "_upsert_host", fake_upsert)

    learned = inventory_learning.learn_result_inventory(
        {
            "target": "jose",
            "hostname": "jose",
            "profile": "linux_generic",
            "identity": {
                "hostname": "jose",
                "os_name": "Ubuntu 24.04",
                "ip_brief": "eth0 UP 192.168.28.10/24",
            },
            "environment_classification": {"environment": "monitoring"},
        },
        resolved_host="192.168.28.10",
        ssh_port=2222,
        settings=_settings(),
    )

    assert learned["saved"] is True
    assert learned["vpn_ip"] == "192.168.28.10"
    assert learned["ssh_port"] == 2222
    assert captured == {
        "vpn_ip": "192.168.28.10",
        "ssh_port": 2222,
        "hostname": "jose",
        "os_name": "Ubuntu 24.04",
        "environment": "monitoring",
        "host_type": "linux_generic",
        "internal_ips": ["192.168.28.10"],
    }


def test_vpn_client_name_becomes_display_name_and_updates_history(monkeypatch) -> None:
    captured = {}
    synced = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="host-vpn",
            vpn_ip=kwargs["vpn_ip"],
            ssh_port=kwargs["ssh_port"],
            hostname=kwargs["hostname"],
            environment=kwargs["environment"],
        )

    def fake_sync(vpn_ip: str, client_name: str) -> int:
        synced.update({"vpn_ip": vpn_ip, "client_name": client_name})
        return 3

    monkeypatch.setattr(inventory_learning, "ensure_database_schema", lambda: [])
    monkeypatch.setattr(inventory_learning, "_upsert_host", fake_upsert)
    monkeypatch.setattr(inventory_learning, "_sync_investigation_display_name", fake_sync)

    learned = inventory_learning.learn_result_inventory(
        {
            "target": "172.27.232.109",
            "hostname": "2com-monitor",
            "profile": "checkmk",
            "identity": {
                "hostname": "2com-monitor",
                "os_name": "Oracle Linux 8",
            },
            "connection": {
                "mode": "vpn_menu",
                "client_name": "HOTBEL MONITOR",
                "ssh_port": 22,
            },
            "environment_classification": {"environment": "monitoring"},
        },
        resolved_host="172.27.232.109",
        ssh_port=22,
        settings=_settings(),
    )

    assert captured["hostname"] == "HOTBEL MONITOR"
    assert learned["client_name"] == "HOTBEL MONITOR"
    assert learned["system_hostname"] == "2com-monitor"
    assert learned["history_updated"] == 3
    assert synced == {"vpn_ip": "172.27.232.109", "client_name": "HOTBEL MONITOR"}


def test_learning_failure_is_returned_without_raising(monkeypatch) -> None:
    monkeypatch.setattr(inventory_learning, "ensure_database_schema", lambda: [])
    monkeypatch.setattr(
        inventory_learning,
        "_upsert_host",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("banco indisponível")),
    )

    learned = inventory_learning.learn_result_inventory(
        {"target": "192.0.2.15", "identity": {"hostname": "srv"}},
        settings=_settings(),
    )

    assert learned["saved"] is False
    assert "banco indisponível" in learned["detail"]
