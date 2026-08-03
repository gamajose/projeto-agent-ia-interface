from __future__ import annotations

import re
from collections import deque
from unittest.mock import MagicMock, patch

from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


class FakeDirectSSHChannel:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []
        self._chunks: deque[bytes] = deque([b"[jose.moraes@2cmvpn01 ~]$ "])

    def recv_ready(self) -> bool:
        return bool(self._chunks)

    def recv(self, _size: int) -> bytes:
        return self._chunks.popleft()

    def close(self) -> None:
        self.closed = True

    def send(self, value: str) -> int:
        self.sent.append(value)
        line = value.strip()
        if line.startswith("ssh "):
            self._chunks.append(
                b"The authenticity of host cannot be established.\r\n"
                b"Are you sure you want to continue connecting (yes/no/[fingerprint])? "
            )
        elif line == "yes":
            self._chunks.append(b"2com@172.27.232.153's password: ")
        elif line == "client-secret":
            self._chunks.append(b"Last login: now\r\n[2com@2com-monitor ~]$ ")
        elif "__AGENT_READY_%s__" in line:
            token = re.findall(r"[0-9a-f]{32}", line)[-1]
            self._chunks.append(f"\r\n__AGENT_READY_{token}__\r\n".encode())
        return len(value)


def test_ssh_command_skips_vpn_menu_index_and_confirmation() -> None:
    channel = FakeDirectSSHChannel()
    bastion = MagicMock()
    bastion.invoke_shell.return_value = channel
    executor = VPNMenuSSHExecutor(
        "172.27.232.153",
        22,
        "2com",
        "client-secret",
        strict_host_key_checking=False,
        bastion_host="10.17.181.1",
        bastion_user="jose.moraes",
        bastion_password="monitor-secret",
        vpn_command="ssh -p {port} {user}@{host}",
        vpn_menu_timeout=10,
    )

    with (
        patch("app.services.vpn_menu_ssh.paramiko.SSHClient", return_value=bastion),
        patch("app.services.vpn_menu_ssh.report_progress"),
    ):
        executor.connect()

    assert "ssh -p 22 2com@172.27.232.153\n" in channel.sent
    assert "yes\n" in channel.sent
    assert "client-secret\n" in channel.sent
    assert "1\n" not in channel.sent
    assert "y\n" not in channel.sent
    assert not any(item.startswith("vpn ") for item in channel.sent)
    assert executor.connection_metadata["mode"] == "ssh_via_monitor"
    assert executor.connection_metadata["vpn_ip"] == "172.27.232.153"
    assert executor.connection_metadata["username"] == "2com"
    assert executor.connection_metadata["ssh_port"] == 22
    assert executor.access_journey[-1]["step"] == "target_shell"
    assert executor.access_journey[-1]["status"] == "completed"
