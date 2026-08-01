from __future__ import annotations

import re
import shlex
import time
from typing import Any
from uuid import uuid4

import paramiko

from app.core.policies import EnvironmentType
from app.services.cancellation import ExecutionCancelled, raise_if_cancelled
from app.services.progress import report_progress
from app.services.redaction import redact_text
from app.services.ssh import CommandResult, SSHExecutor
from app.services.vpn_menu import strip_terminal_codes
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


_HOST_KEY_PROMPT = re.compile(r"Are you sure you want to continue connecting", re.I)
_PASSWORD_PROMPT = re.compile(r"(?:password|senha)\s*:", re.I)
_PERMISSION_DENIED = re.compile(r"Permission denied|Authentication failed", re.I)


class NestedSSHExecutor(SSHExecutor):
    """Executa comandos em um host interno reutilizando o shell do host de entrada.

    O Monitor 1 e o menu VPN são autenticados uma única vez. Cada comando do
    host interno usa um SSH controlado a partir do servidor de entrada, sem
    persistir senha, sem instalar ``sshpass`` e sem deixar shells aninhados
    abertos depois da coleta.
    """

    def __init__(
        self,
        parent: VPNMenuSSHExecutor,
        *,
        host: str,
        port: int,
        username: str,
        password: str | None,
        route: dict[str, Any] | None = None,
        connect_timeout: int = 15,
        strict_host_key_checking: bool = True,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            username=username,
            password=password,
            connect_timeout=connect_timeout,
            allow_agent=False,
            look_for_keys=False,
            strict_host_key_checking=strict_host_key_checking,
        )
        self.parent = parent
        self.route = dict(route or {})
        self.connected = False
        parent_metadata = dict(getattr(parent, "connection_metadata", {}) or {})
        self.connection_metadata: dict[str, Any] = {
            "mode": "ssh_via_host",
            "customer_entry": parent_metadata.get("client_name") or parent.host,
            "entry_address": parent_metadata.get("vpn_ip") or parent.host,
            "entry_port": parent_metadata.get("ssh_port") or parent.port,
            "target_address": self.host,
            "target_port": self.port,
            "username": self.username,
            "route_id": self.route.get("id"),
            "route_path": list(self.route.get("route_path") or []),
            "hops": int(self.route.get("hops") or 1),
        }

    def _ssh_options(self) -> str:
        options = [
            f"-o ConnectTimeout={max(3, int(self.connect_timeout))}",
            "-o ServerAliveInterval=15",
            "-o ServerAliveCountMax=2",
            "-o NumberOfPasswordPrompts=1",
            "-o PreferredAuthentications=password,keyboard-interactive",
            "-o PubkeyAuthentication=no",
        ]
        if self.strict_host_key_checking:
            options.append("-o StrictHostKeyChecking=ask")
        else:
            options.extend(
                [
                    "-o StrictHostKeyChecking=no",
                    "-o UserKnownHostsFile=/dev/null",
                    "-o LogLevel=ERROR",
                ]
            )
        return " ".join(options)

    def _execute_nested(
        self,
        command: str,
        *,
        timeout: int,
        sudo_password: str | None = None,
    ) -> CommandResult:
        channel = self.parent._channel()
        command_id = str(uuid4())
        token = uuid4().hex
        remote_start = f"__AGENT_NESTED_START_{token}__"
        remote_done = f"__AGENT_NESTED_DONE_{token}__"
        route_done = f"__AGENT_ROUTE_DONE_{token}__"
        sudo_marker = f"__AGENT_NESTED_SUDO_{token}__"
        safe_command = redact_text(command)
        started = time.monotonic()

        report_progress(
            "command_started",
            detail=f"Executando em {self.host} via {self.parent.host}: {safe_command}",
            command_id=command_id,
            command=safe_command,
            host=self.host,
            ssh_port=self.port,
            via_host=self.parent.host,
            access_mode="ssh_via_host",
        )

        if sudo_password is not None:
            remote_command = f"sudo -S -p {shlex.quote(sudo_marker)} sh -lc {shlex.quote(command)}"
        else:
            remote_command = command
        remote_payload = (
            f"printf '\\n{remote_start}\\n'; "
            f"{remote_command}; __nested_rc=$?; "
            f"printf '\\n{remote_done}:%s\\n' \"$__nested_rc\""
        )
        destination = f"{self.username}@{self.host}"
        ssh_command = (
            f"ssh -tt {self._ssh_options()} -p {int(self.port)} "
            f"{shlex.quote(destination)} {shlex.quote(remote_payload)}"
        )
        outer_payload = (
            f"{ssh_command}; __route_rc=$?; "
            f"printf '\\n{route_done}:%s\\n' \"$__route_rc\""
        )

        self.parent._drain(quiet_seconds=0.05, limit_seconds=0.3)
        self.parent._send_line(outer_payload)
        raw = ""
        login_password_sent = False
        sudo_password_sent = False
        host_key_confirmed = False
        last_report = 0.0
        deadline = started + max(1, int(timeout))
        remote_pattern = re.compile(re.escape(remote_done) + r":(?P<code>-?\d+)")
        route_pattern = re.compile(re.escape(route_done) + r":(?P<code>-?\d+)")

        try:
            while time.monotonic() < deadline:
                raise_if_cancelled(f"Coleta cancelada durante o salto SSH para {self.host}.")
                changed = False
                while channel.recv_ready():
                    raw += channel.recv(32768).decode(errors="replace")
                    changed = True
                clean = strip_terminal_codes(raw)

                if _HOST_KEY_PROMPT.search(clean) and not host_key_confirmed:
                    self.parent._send_line("yes")
                    host_key_confirmed = True
                    raw = ""
                    continue

                if _PASSWORD_PROMPT.search(clean) and not login_password_sent:
                    if not self.password:
                        raise PermissionError(f"SSH_DEFAULT_PASSWORD não está configurada para acessar {self.host}")
                    self.parent._send_line(self.password)
                    login_password_sent = True
                    raw = ""
                    continue

                if sudo_password is not None and sudo_marker in clean and not sudo_password_sent:
                    self.parent._send_line(sudo_password)
                    sudo_password_sent = True

                route_match = route_pattern.search(clean)
                if route_match:
                    remote_match = remote_pattern.search(clean)
                    exit_code = (
                        int(remote_match.group("code"))
                        if remote_match
                        else int(route_match.group("code"))
                    )
                    start_at = clean.find(remote_start)
                    body_start = start_at + len(remote_start) if start_at >= 0 else 0
                    body_end = remote_match.start() if remote_match else route_match.start()
                    body = clean[body_start:body_end]
                    body = body.replace(sudo_marker, "").strip("\n")
                    if _PERMISSION_DENIED.search(body) and exit_code == 0:
                        exit_code = 255
                    report_progress(
                        "command_completed",
                        status="completed" if exit_code == 0 else "failed",
                        detail=f"Comando no host interno finalizado com código {exit_code}: {safe_command}",
                        command_id=command_id,
                        command=safe_command,
                        exit_code=exit_code,
                        stdout_tail=self._tail(body),
                        stderr_tail="",
                        host=self.host,
                        via_host=self.parent.host,
                        elapsed_seconds=round(time.monotonic() - started, 1),
                    )
                    return CommandResult(command, exit_code, body, "")

                now = time.monotonic()
                if changed and now - last_report >= 0.45:
                    visible = clean
                    start_at = visible.find(remote_start)
                    if start_at >= 0:
                        visible = visible[start_at + len(remote_start):]
                    visible = visible.replace(sudo_marker, "")
                    report_progress(
                        "command_output",
                        detail=f"Recebendo saída de {self.host}: {safe_command}",
                        command_id=command_id,
                        command=safe_command,
                        stdout_tail=self._tail(visible),
                        stderr_tail="",
                        host=self.host,
                        via_host=self.parent.host,
                        elapsed_seconds=round(now - started, 1),
                    )
                    last_report = now

                if channel.closed:
                    raise paramiko.SSHException("a sessão do servidor de entrada foi encerrada durante o salto SSH")
                time.sleep(0.08 if changed else 0.12)
            channel.send("\x03")
            raise TimeoutError(f"comando em {self.host} excedeu o timeout de {timeout}s: {safe_command}")
        except ExecutionCancelled:
            channel.send("\x03")
            report_progress(
                "command_cancelled",
                status="cancelled",
                detail=f"Comando interrompido no host interno {self.host}: {safe_command}",
                command_id=command_id,
                command=safe_command,
                stdout_tail=self._tail(strip_terminal_codes(raw).replace(sudo_marker, "")),
                stderr_tail="",
                host=self.host,
                via_host=self.parent.host,
                elapsed_seconds=round(time.monotonic() - started, 1),
            )
            raise

    def connect(self) -> None:
        raise_if_cancelled(f"Conexão ao host interno {self.host} cancelada pelo operador.")
        report_progress(
            "ssh_connection",
            detail=f"Validando salto SSH de {self.parent.host} para {self.host}:{self.port}.",
            access_step="nested_ssh",
            host=self.host,
            via_host=self.parent.host,
        )
        probe = self._execute_nested("printf 'nested-ready\\n'", timeout=max(10, self.connect_timeout))
        if probe.exit_code != 0 or "nested-ready" not in probe.stdout:
            raise paramiko.SSHException(
                f"o salto SSH para {self.host}:{self.port} não confirmou o shell remoto"
            )
        self.connected = True
        report_progress(
            "ssh_connection",
            status="completed",
            detail=f"Host interno {self.host}:{self.port} acessível pela sessão já aberta.",
            access_step="nested_ssh",
            host=self.host,
            via_host=self.parent.host,
        )

    def close(self) -> None:
        self.connected = False

    def run(
        self,
        command: str,
        environment: EnvironmentType,
        approved: bool = False,
        timeout: int = 60,
    ) -> CommandResult:
        if not self.connected:
            raise RuntimeError("Conexão SSH interna não iniciada.")
        self._validate(command, environment, approved)
        return self._execute_nested(command, timeout=timeout)

    def run_sudo(
        self,
        command: str,
        environment: EnvironmentType,
        approved: bool = False,
        timeout: int = 60,
    ) -> CommandResult:
        if not self.connected:
            raise RuntimeError("Conexão SSH interna não iniciada.")
        self._validate(command, environment, approved)
        if self.username == "root":
            return self._execute_nested(command, timeout=timeout)
        if self.password:
            return self._execute_nested(command, timeout=timeout, sudo_password=self.password)
        return self._execute_nested(
            f"sudo -n sh -lc {shlex.quote(command)}",
            timeout=timeout,
        )
