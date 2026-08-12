from __future__ import annotations

from types import SimpleNamespace

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services import checkmk_site_targeting
from app.services.checkmk_master import (
    _host_environment,
    _host_kind,
    _master_executor,
    _sites_script,
    master_config,
)
from app.services.noc_skills import reload_noc_skills, select_noc_skill


def test_skill_routes_memory_alert_to_linux_memory() -> None:
    reload_noc_skills()
    skill = select_noc_skill(
        {
            "host": "hot-dbstandby",
            "host_address": "192.168.1.19",
            "service": "Memory",
            "output": "Swap: 100.00% - 32.0 GiB of 32.0 GiB",
        },
        host_kind="server",
    )
    assert skill["id"] == "linux-memory-pressure"
    assert skill["target_strategy"] == "internal_ssh"
    assert skill["playbook_id"] == "linux-swap-high"


def test_skill_routes_idrac_snmp_to_entry_context() -> None:
    reload_noc_skills()
    skill = select_noc_skill(
        {
            "host": "hot-dbstandby-idrac",
            "host_address": "192.168.1.252",
            "service": "Check_MK",
            "output": "[snmp] Cannot fetch system description OID",
        },
        host_kind="bmc",
    )
    assert skill["id"] == "snmp-bmc"
    assert skill["target_strategy"] == "entry_context"


def test_zero_address_never_becomes_internal_ssh_target() -> None:
    reload_noc_skills()
    skill = select_noc_skill(
        {
            "host": "checkmk-hot-25",
            "host_address": "0.0.0.0",
            "service": "Process hot automation helpers",
            "output": "Processes: 0",
        },
        host_kind="monitoring_local",
    )
    assert skill["target_strategy"] == "entry_context"


def test_site_targeting_uses_host_only_inside_selected_site(monkeypatch) -> None:
    sites = {
        "hot": SimpleNamespace(
            site_id="hot",
            alias="HOT BEL",
            enabled=True,
            livestatus_host="172.27.232.109",
            livestatus_port=6557,
            status_site="ind",
            status_host="vpn-hotbel-monitor",
            shared_endpoint=False,
        ),
        "dca": SimpleNamespace(
            site_id="dca",
            alias="DCA",
            enabled=True,
            livestatus_host="172.27.232.213",
            livestatus_port=6557,
            status_site="ind",
            status_host="vpn-disalli-monitor",
            shared_endpoint=False,
        ),
    }
    hosts = {
        ("hot", "db01"): SimpleNamespace(
            internal_address="192.168.1.15",
            host_kind="server",
            environment=EnvironmentType.PRODUCTION.value,
        ),
        ("dca", "db01"): SimpleNamespace(
            internal_address="192.168.1.15",
            host_kind="server",
            environment=EnvironmentType.PRODUCTION.value,
        ),
    }

    monkeypatch.setattr(
        checkmk_site_targeting,
        "site_and_host",
        lambda site_id, host_name: (sites.get(site_id), hosts.get((site_id, host_name))),
    )
    monkeypatch.setattr(
        checkmk_site_targeting,
        "select_noc_skill",
        lambda event, host_kind=None: {
            "id": "generic",
            "title": "Generic",
            "target_strategy": "internal_ssh",
            "playbook_id": None,
        },
    )

    hot = checkmk_site_targeting.resolve_checkmk_site_target(
        {"site_id": "hot", "host": "db01", "host_address": "10.0.0.99", "service": "Memory"}
    )
    dca = checkmk_site_targeting.resolve_checkmk_site_target(
        {"site_id": "dca", "host": "db01", "host_address": "10.0.0.99", "service": "Memory"}
    )

    assert hot["entry_address"] == "172.27.232.109"
    assert dca["entry_address"] == "172.27.232.213"
    assert hot["internal_address"] == "192.168.1.15"
    assert dca["internal_address"] == "192.168.1.15"
    assert hot["scope_key"] == "hot:db01"
    assert dca["scope_key"] == "dca:db01"
    assert hot["scope_key"] != dca["scope_key"]


def test_shared_monitor_endpoint_is_guarded(monkeypatch) -> None:
    site = SimpleNamespace(
        site_id="aha",
        alias="AHAV",
        enabled=True,
        livestatus_host="10.17.181.43",
        livestatus_port=6561,
        status_site="ind",
        status_host="monitor-cloud-aha",
        shared_endpoint=True,
    )
    host = SimpleNamespace(
        internal_address="192.168.1.10",
        host_kind="server",
        environment=EnvironmentType.UNKNOWN.value,
    )
    monkeypatch.setattr(checkmk_site_targeting, "site_and_host", lambda *_: (site, host))
    monkeypatch.setattr(
        checkmk_site_targeting,
        "select_noc_skill",
        lambda event, host_kind=None: {"id": "generic", "target_strategy": "internal_ssh"},
    )

    route = checkmk_site_targeting.resolve_checkmk_site_target(
        {"site_id": "aha", "host": "app01", "service": "Memory"}
    )
    assert route["valid"] is True
    assert route["shared_endpoint"] is True
    assert route["auto_investigate"] is False
    assert route["strategy"] == "site_guard"


def test_master_parser_script_never_exports_site_secret() -> None:
    source = _sites_script(settings=get_settings())
    assert 'cfg.get("secret")' not in source
    assert '"secret":' not in source
    assert '"site_id"' in source
    assert '"livestatus_host"' in source
    assert '"status_host"' in source


def test_cmk05_reuses_shared_access_credentials() -> None:
    settings = get_settings().model_copy(
        update={
            "secret_backend": "env",
            "ssh_bastion_user": "shared-access-user",
            "ssh_bastion_password": "shared-access-password",
            "ssh_cmk05": "10.17.181.44",
        }
    )

    cfg = master_config(settings)
    executor = _master_executor(settings)

    assert cfg["ssh_user"] == "shared-access-user"
    assert executor.username == "shared-access-user"
    assert executor.password == "shared-access-password"
    assert "CHECKMK_MASTER_SSH_USER" not in master_config.__code__.co_consts


def test_checkmk_host_classification_handles_real_examples() -> None:
    assert _host_kind("checkmk-hot-25", "0.0.0.0") == "monitoring_local"
    assert _host_kind("hot-dbstandby-idrac", "192.168.1.252") == "bmc"
    assert _host_kind("hot-firewall", "192.168.1.1") == "firewall"
    assert _host_kind("hot-dbstandby", "192.168.1.19") == "server"
    assert _host_environment("hot-dbstandby", "server") == EnvironmentType.STANDBY.value
    assert _host_environment("hot-dbprimario-oda", "server") == EnvironmentType.PRODUCTION.value
