from __future__ import annotations

import re

import pytest

from app.core.policies import EnvironmentType
from app.services.nested_ssh import NestedSSHExecutor


class FakeChannel:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False
        self.sent: list[str] = []

    def push(self, value: str) -> None:
        self.buffer.extend(value.encode())

    def recv_ready(self) -> bool:
        return bool(self.buffer)

    def recv(self, size: int) -> bytes:
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def send(self, value: str) -> int:
        self.sent.append(value)
        return len(value)


class FakeParent:
    def __init__(self) -> None:
        self.host = "172.27.232.109"
        self.port = 22
        self.password = "secret"
        self.connection_metadata = {
            "client_name": "EMPRESA JOSÉ MONITOR",
            "vpn_ip": self.host,
            "ssh_port": 22,
        }
        self.channel = FakeChannel()
        self.sent: list[str] = []
        self.state = "idle"
        self.first_connection = True
        self.token = ""
        self.sudo_marker: str | None = None
        self.output = "ok"

    def _channel(self) -> FakeChannel:
        return self.channel

    def _drain(self, **_kwargs) -> str:
        return ""

    @staticmethod
    def _tail(value: str, limit: int = 6000) -> str:
        return value[-limit:]

    def _finish(self) -> None:
        self.channel.push(
            f"\n__AGENT_NESTED_START_{self.token}__\n"
            f"{self.output}\n"
            f"__AGENT_NESTED_DONE_{self.token}__:0\n"
            f"__AGENT_ROUTE_DONE_{self.token}__:0\n"
        )
        self.state = "idle"
        self.first_connection = False

    def _send_line(self, value: str) -> None:
        self.sent.append(value)
        if "ssh -tt" in value:
            match = re.search(r"__AGENT_NESTED_START_([a-f0-9]+)__", value)
            assert match, value
            self.token = match.group(1)
            sudo_match = re.search(r"__AGENT_NESTED_SUDO_[a-f0-9]+__", value)
            self.sudo_marker = sudo_match.group(0) if sudo_match else None
            if "nested-ready" in value:
                self.output = "nested-ready"
            elif "uname -s" in value:
                self.output = "Linux"
            elif "id -u" in value:
                self.output = "0"
            else:
                self.output = "ok"
            if self.first_connection:
                self.channel.push("Are you sure you want to continue connecting (yes/no)? ")
                self.state = "host_key"
            else:
                self.channel.push("2com@10.45.1.24's password: ")
                self.state = "login_password"
            return
        if self.state == "host_key" and value == "yes":
            self.channel.push("2com@10.45.1.24's password: ")
            self.state = "login_password"
            return
        if self.state == "login_password":
            assert value == "secret"
            if self.sudo_marker:
                self.channel.push(self.sudo_marker)
                self.state = "sudo_password"
            else:
                self._finish()
            return
        if self.state == "sudo_password":
            assert value == "secret"
            self._finish()


def _executor(parent: FakeParent) -> NestedSSHExecutor:
    return NestedSSHExecutor(
        parent,  # type: ignore[arg-type]
        host="10.45.1.24",
        port=22,
        username="2com",
        password="secret",
        connect_timeout=3,
        strict_host_key_checking=True,
    )


def test_nested_ssh_reuses_parent_and_handles_host_key_and_password() -> None:
    parent = FakeParent()
    executor = _executor(parent)

    executor.connect()
    result = executor.run("uname -s", EnvironmentType.PRODUCTION)

    assert executor.connected is True
    assert result.exit_code == 0
    assert "Linux" in result.stdout
    assert "yes" in parent.sent
    assert parent.sent.count("secret") == 2
    assert executor.connection_metadata["mode"] == "ssh_via_host"
    assert executor.connection_metadata["entry_address"] == "172.27.232.109"
    assert executor.connection_metadata["target_address"] == "10.45.1.24"


def test_nested_sudo_uses_same_operational_password() -> None:
    parent = FakeParent()
    executor = _executor(parent)
    executor.connect()
    before = parent.sent.count("secret")

    result = executor.run_sudo("id -u", EnvironmentType.MONITORING)

    assert result.exit_code == 0
    assert "0" in result.stdout
    assert parent.sent.count("secret") == before + 2


def test_database_client_is_blocked_before_nested_command_is_sent() -> None:
    parent = FakeParent()
    executor = _executor(parent)
    executor.connect()
    sent_before = len(parent.sent)

    with pytest.raises(PermissionError, match="CUSTOMER_DATABASE_ACCESS_DENIED"):
        executor.run("psql -c 'select 1'", EnvironmentType.PRODUCTION)

    assert len(parent.sent) == sent_before
