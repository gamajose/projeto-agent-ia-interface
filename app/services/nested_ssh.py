from __future__ import annotations

import re
import shlex
import time
from typing import Any
from uuid import uuid4

import paramiko

from app.core.policies import EnvironmentType
from app.services.cancellation import ExecutionCancelled, raise_if_cancelled
from app.services.investigation_budget import reserve_command
from app.services.metrics import increment, observe
from app.services.performance_config import get_performance_config
from app.services.progress import report_progress
from app.services.redaction import redact_text
from app.services.ssh import CommandResult, SSHExecutor
from app.services.vpn_menu import strip_terminal_codes
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


_HOST_KEY_PROMPT = re.compile(r"Are you sure you want to continue connecting", re.I)
_PASSWORD_PROMPT = re.compile(r"(?:password|senha)\s*:", re.I)
_PERMISSION_DENIED = re.compile(r"Permission denied|Authentication failed", re.I)


class NestedSSHExecutor(SSHExecutor):
    """Executa comandos internos reutilizando a sessão do servidor de entrada.

    Quando habilitado, o primeiro login tenta criar um ControlMaster temporário
    no host de entrada. Se o OpenSSH remoto não suportar multiplexação, o Agent
    volta automaticamente ao modo compatível, com um SSH controlado por comando.
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
        self.performance = get_performance_config()
        self.control_path = f"/tmp/agent-ia-{uuid4().hex}.sock"
        self.master_active = False
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
            "persistent_channel": False,
        }

    @property
    def destination(self) -> str:
        return f"{self.username}@{self.host}"

    def _base_ssh_options(self) -> list[str]:
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
        return options

    def _ssh_options(self, *, use_master: bool) -> str:
        options = self._base_ssh_options()
        if use_master:
            options.extend(
                [
                    "-o ControlMaster=no",
                    f"-o ControlPath={shlex.quote(self.control_path)}",
                ]
            )
        return " ".join(options)

    def _interactive_command(
        self,
        outer_payload: str,
        *,
        timeout: int,
        marker: str,
        allow_login_password: bool,
        sudo_marker: str | None = None,
        sudo_password: str | None = None,
        command_id: str | None = None,
        safe_command: str = "",
    ) -> tuple[str, int]:
        channel = self.parent._channel()
        self.parent._drain(quiet_seconds=0.05, limit_seconds=0.3)
        self.parent._send_line(outer_payload)
        raw = ""
        login_password_sent = False
        sudo_password_sent = False
        host_key_confirmed = False
        last_report = 0.0
        started = time.monotonic()
        deadline = started + max(1, int(timeout))
        pattern = re.compile(re.escape(marker) + r":(?P<code>-?\d+)")

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

            if allow_login_password and _PASSWORD_PROMPT.search(clean) and not login_password_sent:
                if not self.password:
                    raise PermissionError(f"SSH_DEFAULT_PASSWORD não está configurada para acessar {self.host}")
                self.parent._send_line(self.password)
                login_password_sent = True
                raw = ""
                continue

            if sudo_marker and sudo_password is not None and sudo_marker in clean and not sudo_password_sent:
                self.parent._send_line(sudo_password)
                sudo_password_sent = True

            match = pattern.search(clean)
            if match:
                return clean[: match.start()], int(match.group("code"))

            now = time.monotonic()
            if command_id and changed and now - last_report >= 0.45:
                visible = clean.replace(sudo_marker or "", "")
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
        raise TimeoutError(f"operação SSH em {self.host} excedeu o timeout de {timeout}s")

    def _start_master(self) -> None:
        token = uuid4().hex
        marker = f"__AGENT_MASTER_{token}__"
        options = " ".join(
            [
                *self._base_ssh_options(),
                "-o ControlMaster=yes",
                f"-o ControlPath={shlex.quote(self.control_path)}",
                f"-o ControlPersist={self.performance.nested_ssh_control_persist_seconds}",
                "-o ExitOnForwardFailure=yes",
            ]
        )
        command = (
            f"rm -f {shlex.quote(self.control_path)}; "
            f"ssh -M -fNT {options} -p {int(self.port)} {shlex.quote(self.destination)}; "
            f"__master_rc=$?; printf '\n{marker}:%s\n' \"$__master_rc\""
        )
        _body, exit_code = self._interactive_command(
            command,
            timeout=max(2, min(8, self.connect_timeout + 2)),
            marker=marker,
            allow_login_password=True,
        )
        if exit_code != 0:
            raise paramiko.SSHException(
                f"não foi possível criar o canal SSH persistente para {self.host}:{self.port}"
            )
        self.master_active = True
        self.connection_metadata["persistent_channel"] = True
        self.connection_metadata["control_persist_seconds"] = (
            self.performance.nested_ssh_control_persist_seconds
        )
        self.connection_metadata.pop("persistent_channel_fallback", None)
        increment("agent_nested_ssh_master", labels={"status": "created"})

    def _fallback_from_master(self, exc: Exception) -> None:
        self.master_active = False
        self.connection_metadata["persistent_channel"] = False
        self.connection_metadata["persistent_channel_fallback"] = (
            f"{type(exc).__name__}: {redact_text(str(exc))}"
        )
        increment("agent_nested_ssh_master", labels={"status": "fallback"})
        report_progress(
            "ssh_connection",
            detail=(
                "O servidor de entrada não confirmou a multiplexação SSH. "
                "Continuando em modo compatível, sem interromper a investigação."
            ),
            access_step="nested_ssh_fallback",
            host=self.host,
            via_host=self.parent.host,
            persistent_channel=False,
        )
        try:
            self.parent._send_line(f"rm -f {shlex.quote(self.control_path)}")
            self.parent._drain(quiet_seconds=0.05, limit_seconds=0.5)
        except Exception:
            pass

    def _execute_nested(
        self,
        command: str,
        *,
        timeout: int,
        sudo_password: str | None = None,
        count_budget: bool = True,
    ) -> CommandResult:
        if count_budget:
            timeout = reserve_command(self.host, timeout)
        command_id = str(uuid4())
        token = uuid4().hex
        remote_start = f"__AGENT_NESTED_START_{token}__"
        remote_done = f"__AGENT_NESTED_DONE_{token}__"
        route_done = f"__AGENT_ROUTE_DONE_{token}__"
        sudo_marker = f"__AGENT_NESTED_SUDO_{token}__"
        safe_command = redact_text(command)
        started = time.monotonic()
        increment(
            "agent_tool_executions",
            labels={"transport": "nested_ssh", "persistent": str(self.master_active).lower()},
        )

        report_progress(
            "command_started",
            detail=f"Executando em {self.host} via {self.parent.host}: {safe_command}",
            command_id=command_id,
            command=safe_command,
            host=self.host,
            ssh_port=self.port,
            via_host=self.parent.host,
            access_mode="ssh_via_host",
            persistent_channel=self.master_active,
        )

        if sudo_password is not None:
            remote_command = f"sudo -S -p {shlex.quote(sudo_marker)} sh -lc {shlex.quote(command)}"
        else:
            remote_command = command
        remote_payload = (
            f"printf '\n{remote_start}\n'; "
            f"{remote_command}; __nested_rc=$?; "
            f"printf '\n{remote_done}:%s\n' \"$__nested_rc\""
        )
        ssh_command = (
            f"ssh -tt {self._ssh_options(use_master=self.master_active)} -p {int(self.port)} "
            f"{shlex.quote(self.destination)} {shlex.quote(remote_payload)}"
        )
        outer_payload = (
            f"{ssh_command}; __route_rc=$?; "
            f"printf '\n{route_done}:%s\n' \"$__route_rc\""
        )

        try:
            body, route_code = self._interactive_command(
                outer_payload,
                timeout=timeout,
                marker=route_done,
                allow_login_password=not self.master_active,
                sudo_marker=sudo_marker,
                sudo_password=sudo_password,
                command_id=command_id,
                safe_command=safe_command,
            )
            remote_pattern = re.compile(re.escape(remote_done) + r":(?P<code>-?\d+)")
            remote_match = remote_pattern.search(body)
            exit_code = int(remote_match.group("code")) if remote_match else route_code
            start_at = body.find(remote_start)
            body_start = start_at + len(remote_start) if start_at >= 0 else 0
            body_end = remote_match.start() if remote_match else len(body)
            output = body[body_start:body_end].replace(sudo_marker, "").strip("\n")
            if _PERMISSION_DENIED.search(output) and exit_code == 0:
                exit_code = 255
            increment(
                "agent_tool_results",
                labels={"transport": "nested_ssh", "status": "success" if exit_code == 0 else "failed"},
            )
            report_progress(
                "command_completed",
                status="completed" if exit_code == 0 else "failed",
                detail=f"Comando no host interno finalizado com código {exit_code}: {safe_command}",
                command_id=command_id,
                command=safe_command,
                exit_code=exit_code,
                stdout_tail=self._tail(output),
                stderr_tail="",
                host=self.host,
                via_host=self.parent.host,
                persistent_channel=self.master_active,
                elapsed_seconds=round(time.monotonic() - started, 1),
            )
            return CommandResult(command, exit_code, output, "")
        except ExecutionCancelled:
            increment("agent_tool_results", labels={"transport": "nested_ssh", "status": "cancelled"})
            report_progress(
                "command_cancelled",
                status="cancelled",
                detail=f"Comando interrompido no host interno {self.host}: {safe_command}",
                command_id=command_id,
                command=safe_command,
                host=self.host,
                via_host=self.parent.host,
                elapsed_seconds=round(time.monotonic() - started, 1),
            )
            raise
        except Exception:
            increment("agent_tool_results", labels={"transport": "nested_ssh", "status": "exception"})
            raise
        finally:
            observe(
                "agent_tool_duration_seconds",
                time.monotonic() - started,
                labels={"transport": "nested_ssh"},
            )

    def connect(self) -> None:
        raise_if_cancelled(f"Conexão ao host interno {self.host} cancelada pelo operador.")
        started = time.monotonic()
        report_progress(
            "ssh_connection",
            detail=f"Validando salto SSH de {self.parent.host} para {self.host}:{self.port}.",
            access_step="nested_ssh",
            host=self.host,
            via_host=self.parent.host,
        )
        try:
            if self.performance.nested_ssh_master_enabled:
                try:
                    self._start_master()
                except ExecutionCancelled:
                    raise
                except (TimeoutError, paramiko.SSHException, OSError) as exc:
                    self._fallback_from_master(exc)
            probe = self._execute_nested(
                "printf 'nested-ready\n'",
                timeout=max(10, self.connect_timeout),
                count_budget=False,
            )
            if probe.exit_code != 0 or "nested-ready" not in probe.stdout:
                raise paramiko.SSHException(
                    f"o salto SSH para {self.host}:{self.port} não confirmou o shell remoto"
                )
            self.connected = True
            increment(
                "agent_ssh_connections",
                labels={"mode": "nested", "status": "success", "persistent": str(self.master_active).lower()},
            )
            report_progress(
                "ssh_connection",
                status="completed",
                detail=(
                    f"Host interno {self.host}:{self.port} acessível por canal SSH persistente."
                    if self.master_active
                    else f"Host interno {self.host}:{self.port} acessível em modo SSH compatível."
                ),
                access_step="nested_ssh",
                host=self.host,
                via_host=self.parent.host,
                persistent_channel=self.master_active,
            )
        except Exception:
            increment("agent_ssh_connections", labels={"mode": "nested", "status": "failed"})
            self.close()
            raise
        finally:
            observe(
                "agent_ssh_connection_duration_seconds",
                time.monotonic() - started,
                labels={"mode": "nested"},
            )

    def close(self) -> None:
        if self.master_active:
            token = uuid4().hex
            marker = f"__AGENT_MASTER_CLOSE_{token}__"
            command = (
                f"ssh -S {shlex.quote(self.control_path)} -O exit {shlex.quote(self.destination)} "
                f">/dev/null 2>&1; __close_rc=$?; rm -f {shlex.quote(self.control_path)}; "
                f"printf '\n{marker}:%s\n' \"$__close_rc\""
            )
            try:
                self._interactive_command(
                    command,
                    timeout=10,
                    marker=marker,
                    allow_login_password=False,
                )
                increment("agent_nested_ssh_master", labels={"status": "closed"})
            except Exception:
                increment("agent_nested_ssh_master", labels={"status": "cleanup_failed"})
            finally:
                self.master_active = False
                self.connection_metadata["persistent_channel"] = False
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
