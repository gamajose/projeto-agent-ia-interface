from unittest.mock import MagicMock, patch

from app.core.policies import EnvironmentType
from app.core.settings import Settings
from app.services.runner import ResolvedTarget, build_executor
from app.services.ssh import SSHExecutor
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


def _settings_from_legacy_vpn_env(monkeypatch) -> Settings:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+psycopg://agent:secret@127.0.0.1/agent")
    monkeypatch.setenv("SSH_DEFAULT_USER", "2com")
    monkeypatch.setenv("SSH_DEFAULT_PASSWORD", "client-password")
    monkeypatch.setenv("SSH_SRV_VPN_IP", "10.17.181.1")
    monkeypatch.setenv("SSH_SRV_VPN_PORT", "22")
    monkeypatch.setenv("SSH_SRV_VPN_USER", "jose.moraes")
    monkeypatch.setenv("SSH_SRV_VPN_SENHA", "vpn-password")
    return Settings(_env_file=None)


def _target() -> ResolvedTarget:
    return ResolvedTarget(
        reference="cliente-vpn",
        host="172.27.232.205",
        port=22,
        environment=EnvironmentType.PRODUCTION,
        inventory=None,
    )


def test_legacy_vpn_environment_names_configure_bastion(monkeypatch):
    settings = _settings_from_legacy_vpn_env(monkeypatch)

    assert settings.ssh_bastion_host == "10.17.181.1"
    assert settings.ssh_bastion_port == 22
    assert settings.ssh_bastion_user == "jose.moraes"
    assert settings.ssh_bastion_password == "vpn-password"


def test_legacy_vpn_environment_uses_interactive_menu_by_default(monkeypatch):
    settings = _settings_from_legacy_vpn_env(monkeypatch)
    monkeypatch.setenv("SSH_FIREWALL_PF_USER", "root")
    monkeypatch.setenv("SSH_FIREWALL_PF_PASSWORD", "pf-password")
    monkeypatch.setenv("SSH_FIREWALL_PF_PORT", "2224")

    executor = build_executor(_target(), settings=settings)

    assert isinstance(executor, VPNMenuSSHExecutor)
    assert executor.bastion_host == "10.17.181.1"
    assert executor.bastion_user == "jose.moraes"
    assert executor.bastion_password == "vpn-password"
    assert executor.username == "2com"
    assert executor.password == "client-password"
    assert executor.firewall_user == "root"
    assert executor.firewall_password == "pf-password"
    assert executor.firewall_port == 2224


def test_direct_tcpip_remains_available_when_explicit(monkeypatch):
    settings = _settings_from_legacy_vpn_env(monkeypatch)
    monkeypatch.setenv("SSH_ACCESS_MODE", "direct")
    executor = build_executor(_target(), settings=settings)

    assert isinstance(executor, SSHExecutor)
    assert not isinstance(executor, VPNMenuSSHExecutor)

    bastion_client = MagicMock()
    target_client = MagicMock()
    transport = MagicMock()
    channel = MagicMock()
    transport.is_active.return_value = True
    transport.open_channel.return_value = channel
    bastion_client.get_transport.return_value = transport

    with patch(
        "app.services.ssh.paramiko.SSHClient",
        side_effect=[bastion_client, target_client],
    ):
        executor.connect()

    transport.open_channel.assert_called_once_with(
        "direct-tcpip",
        ("172.27.232.205", 22),
        ("127.0.0.1", 0),
        timeout=settings.ssh_connect_timeout,
    )
    assert target_client.connect.call_args.kwargs["sock"] is channel
