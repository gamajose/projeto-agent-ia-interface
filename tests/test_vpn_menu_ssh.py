from __future__ import annotations

import re
from collections import deque
from unittest.mock import MagicMock, patch

from app.core.policies import EnvironmentType
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


class FakeInteractiveChannel:
    def __init__(self, *, pfsense: bool = False) -> None:
        self.pfsense = pfsense
        self.closed = False
        self.sent: list[str] = []
        self._chunks: deque[bytes] = deque([b"monitor1$ "])

    def recv_ready(self) -> bool:
        return bool(self._chunks)

    def recv(self, _size: int) -> bytes:
        return self._chunks.popleft()

    def close(self) -> None:
        self.closed = True

    def send(self, value: str) -> int:
        self.sent.append(value)
        line = value.strip()
        if line.startswith("vpn "):
            if self.pfsense:
                self._chunks.append(
                    b"N IP_VPN NOME_CLIENTE PORTA CLIENT\r\n"
                    b"[1] 172.27.226.57 ATACADAO CENTRAL (PF) 2224\\3556\\HTTP CLIENT226057\r\n"
                    b"Qual o Numero do Servidor Para Acesso (Q Para sair): "
                )
            else:
                self._chunks.append(
                    b"N IP_VPN NOME_CLIENTE PORTA CLIENT\r\n"
                    b"[1] 172.27.200.10 OUTRO CLIENTE (MONITOR) DEFAULT CLIENT200010\r\n"
                    b"[2] 172.27.233.38 IBIAPABA (MONITOR) DEFAULT CLIENT233038\r\n"
                    b"Qual o Numero do Servidor Para Acesso (Q Para sair): "
                )
        elif line in {"1", "2"}:
            self._chunks.append(b"Deseja Acessar o Servidor ? - [Y|N]: ")
        elif line == "y":
            user = b"root" if self.pfsense else b"2com"
            self._chunks.append(b"Enter SSH password for User " + user + b":")
        elif line in {"server-secret", "pf-secret"}:
            if self.pfsense:
                self._chunks.append(b"*** Welcome to pfSense ***\r\n8) Shell\r\nEnter an option: ")
            else:
                self._chunks.append(b"Last login: now\r\n[2com@cliente ~]$ ")
        elif self.pfsense and line == "8":
            self._chunks.append(b"[2.6.0-RELEASE][root@utm]/root: ")
        elif "__AGENT_READY_%s__" in line:
            token = re.findall(r"[0-9a-f]{32}", line)[-1]
            self._chunks.append(f"\r\n__AGENT_READY_{token}__\r\n".encode())
        elif "__AGENT_START_%s__" in line:
            token = re.findall(r"[0-9a-f]{32}", line)[0]
            output = "FreeBSD" if self.pfsense else "Linux"
            self._chunks.append(
                f"\n__AGENT_START_{token}__\n{output}\n__AGENT_DONE_{token}__:0\n".encode()
            )
        return len(value)


def _executor(channel: FakeInteractiveChannel) -> tuple[VPNMenuSSHExecutor, MagicMock]:
    bastion = MagicMock()
    bastion.invoke_shell.return_value = channel
    executor = VPNMenuSSHExecutor(
        "172.27.226.57" if channel.pfsense else "172.27.233.38",
        22,
        "2com",
        "server-secret",
        strict_host_key_checking=False,
        bastion_host="10.17.181.1",
        bastion_user="jose.moraes",
        bastion_password="vpn-secret",
        firewall_user="root",
        firewall_password="pf-secret",
        firewall_port=2224,
        vpn_menu_timeout=10,
    )
    return executor, bastion


def _access_steps(progress: MagicMock) -> list[str]:
    return [
        str(call.kwargs["access_step"])
        for call in progress.call_args_list
        if call.args and call.args[0] == "ssh_connection" and call.kwargs.get("access_step")
    ]


def test_connects_through_menu_selects_matching_line_and_runs_command() -> None:
    channel = FakeInteractiveChannel()
    executor, bastion = _executor(channel)

    with (
        patch("app.services.vpn_menu_ssh.paramiko.SSHClient", return_value=bastion),
        patch("app.services.vpn_menu_ssh.report_progress") as progress,
    ):
        executor.connect()
        result = executor.run("uname -s", EnvironmentType.MONITORING)

    assert result.exit_code == 0
    assert result.stdout == "Linux"
    assert executor.connection_metadata["client_name"] == "IBIAPABA MONITOR"
    assert executor.connection_metadata["vpn_index"] == 2
    assert executor.connection_metadata["ssh_port"] == 22
    assert "vpn 172.27.233.38\n" in channel.sent
    assert "2\n" in channel.sent
    assert "y\n" in channel.sent
    assert "server-secret\n" in channel.sent
    bastion.connect.assert_called_once()

    steps = _access_steps(progress)
    assert steps.index("bastion") < steps.index("inventory")
    assert steps.index("inventory") < steps.index("selection")
    assert steps.index("selection") < steps.index("confirmation")
    assert steps.index("confirmation") < steps.index("authentication")
    assert steps.index("authentication") < steps.index("target_shell")
    journey = executor.connection_metadata["access_journey"]
    assert journey[-1]["step"] == "target_shell"
    assert journey[-1]["status"] == "completed"


def test_pfsense_uses_firewall_password_and_enters_shell_option_8() -> None:
    channel = FakeInteractiveChannel(pfsense=True)
    executor, bastion = _executor(channel)

    with (
        patch("app.services.vpn_menu_ssh.paramiko.SSHClient", return_value=bastion),
        patch("app.services.vpn_menu_ssh.report_progress") as progress,
    ):
        executor.connect()
        result = executor.run_sudo("uname -s", EnvironmentType.MONITORING)

    assert result.exit_code == 0
    assert result.stdout == "FreeBSD"
    assert executor.remote_username == "root"
    assert executor.port == 2224
    assert executor.connection_metadata["client_name"] == "ATACADAO CENTRAL PF"
    assert executor.connection_metadata["is_pfsense"] is True
    assert "pf-secret\n" in channel.sent
    assert "8\n" in channel.sent
    assert not any("sudo -S" in item for item in channel.sent)
    assert "pfsense_shell" in _access_steps(progress)
    assert any(
        item["step"] == "pfsense_shell" and item["status"] == "completed"
        for item in executor.connection_metadata["access_journey"]
    )
