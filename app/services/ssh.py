from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import time
from uuid import uuid4

import paramiko

from app.core.policies import EnvironmentType, classify_command, evaluate_action
from app.services.cancellation import ExecutionCancelled, raise_if_cancelled
from app.services.correction_policy import validate_correction
from app.services.investigation_budget import reserve_command
from app.services.metrics import increment, observe
from app.services.progress import report_progress
from app.services.redaction import redact_text


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


class SSHExecutor:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        connect_timeout: int = 15,
        *,
        private_key_path: str | None = None,
        private_key_passphrase: str | None = None,
        allow_agent: bool = True,
        look_for_keys: bool = True,
        strict_host_key_checking: bool = True,
        known_hosts_path: str = "~/.ssh/known_hosts",
        bastion_host: str | None = None,
        bastion_port: int = 22,
        bastion_user: str | None = None,
        bastion_password: str | None = None,
        bastion_private_key_path: str | None = None,
        bastion_private_key_passphrase: str | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self.private_key_path = str(Path(private_key_path).expanduser()) if private_key_path else None
        self.private_key_passphrase = private_key_passphrase
        self.allow_agent = allow_agent
        self.look_for_keys = look_for_keys
        self.strict_host_key_checking = strict_host_key_checking
        self.known_hosts_path = str(Path(known_hosts_path).expanduser())
        self.bastion_host = bastion_host
        self.bastion_port = bastion_port
        self.bastion_user = bastion_user
        self.bastion_password = bastion_password
        self.bastion_private_key_path = str(Path(bastion_private_key_path).expanduser()) if bastion_private_key_path else None
        self.bastion_private_key_passphrase = bastion_private_key_passphrase
        self.client: paramiko.SSHClient | None = None
        self.bastion_client: paramiko.SSHClient | None = None

    def _configure_host_keys(self, client: paramiko.SSHClient) -> None:
        client.load_system_host_keys()
        known_hosts = Path(self.known_hosts_path)
        if known_hosts.exists():
            client.load_host_keys(str(known_hosts))
        if self.strict_host_key_checking:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _common_connect_args(self) -> dict:
        return {
            "timeout": self.connect_timeout,
            "auth_timeout": self.connect_timeout,
            "banner_timeout": self.connect_timeout,
            "allow_agent": self.allow_agent,
            "look_for_keys": self.look_for_keys,
        }

    def connect(self) -> None:
        raise_if_cancelled("Conexão SSH cancelada pelo operador.")
        started = time.monotonic()
        labels = {"mode": "bastion" if self.bastion_host else "direct"}
        sock = None
        try:
            if self.bastion_host:
                bastion = paramiko.SSHClient()
                self._configure_host_keys(bastion)
                bastion.connect(
                    hostname=self.bastion_host,
                    port=self.bastion_port,
                    username=self.bastion_user or self.username,
                    password=self.bastion_password or None,
                    key_filename=self.bastion_private_key_path,
                    passphrase=self.bastion_private_key_passphrase,
                    **self._common_connect_args(),
                )
                transport = bastion.get_transport()
                if transport is None or not transport.is_active():
                    bastion.close()
                    raise paramiko.SSHException("transporte do bastion não ficou ativo")
                sock = transport.open_channel(
                    "direct-tcpip",
                    (self.host, self.port),
                    ("127.0.0.1", 0),
                    timeout=self.connect_timeout,
                )
                self.bastion_client = bastion

            raise_if_cancelled("Conexão SSH cancelada pelo operador.")
            client = paramiko.SSHClient()
            self._configure_host_keys(client)
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password or None,
                key_filename=self.private_key_path,
                passphrase=self.private_key_passphrase,
                sock=sock,
                **self._common_connect_args(),
            )
            self.client = client
            increment("agent_ssh_connections", labels={**labels, "status": "success"})
        except Exception:
            increment("agent_ssh_connections", labels={**labels, "status": "failed"})
            raise
        finally:
            observe("agent_ssh_connection_duration_seconds", time.monotonic() - started, labels=labels)

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
        if self.bastion_client:
            self.bastion_client.close()
            self.bastion_client = None

    def _validate(self, command: str, environment: EnvironmentType, approved: bool) -> None:
        action = classify_command(command)
        decision = evaluate_action(action, environment)
        if not decision.allowed:
            raise PermissionError(f"{decision.policy_code}: {decision.reason}")
        if decision.requires_approval and not approved:
            raise PermissionError(f"{decision.policy_code}: aprovação explícita necessária")

        if approved:
            correction = validate_correction(command)
            if not correction.allowed:
                raise PermissionError(f"CORRECTION_POLICY_BLOCKED: {correction.reason}")

    @staticmethod
    def _tail(value: str, limit: int = 6000) -> str:
        return redact_text(value[-limit:])

    def _execute_streaming(
        self,
        *,
        command: str,
        wrapped_command: str,
        timeout: int,
        sudo_password: str | None = None,
    ) -> CommandResult:
        if not self.client:
            raise RuntimeError("Conexão SSH não iniciada.")

        timeout = reserve_command(self.host, timeout)
        command_id = str(uuid4())
        safe_command = redact_text(command)
        started = time.monotonic()
        increment("agent_tool_executions", labels={"transport": "ssh", "host": self.host})
        report_progress(
            "command_started",
            detail=f"Executando: {safe_command}",
            command_id=command_id,
            command=safe_command,
            host=self.host,
            ssh_port=self.port,
        )

        stdin, stdout, _stderr = self.client.exec_command(
            wrapped_command,
            timeout=max(1, int(timeout)),
            get_pty=False,
        )
        if sudo_password is not None:
            stdin.write(sudo_password + "\n")
            stdin.flush()
            stdin.channel.shutdown_write()

        channel = stdout.channel
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        last_report = 0.0
        last_reported_size = 0

        try:
            while True:
                raise_if_cancelled(f"Coleta cancelada durante o comando: {safe_command}")
                elapsed = time.monotonic() - started
                if elapsed > max(1, int(timeout)):
                    channel.close()
                    raise TimeoutError(f"comando excedeu o timeout de {timeout}s: {safe_command}")

                changed = False
                while channel.recv_ready():
                    stdout_chunks.append(channel.recv(32768).decode(errors="replace"))
                    changed = True
                while channel.recv_stderr_ready():
                    stderr_chunks.append(channel.recv_stderr(32768).decode(errors="replace"))
                    changed = True

                total_size = sum(len(item) for item in stdout_chunks) + sum(len(item) for item in stderr_chunks)
                now = time.monotonic()
                if changed and total_size != last_reported_size and now - last_report >= 0.45:
                    report_progress(
                        "command_output",
                        detail=f"Recebendo saída de: {safe_command}",
                        command_id=command_id,
                        command=safe_command,
                        stdout_tail=self._tail("".join(stdout_chunks)),
                        stderr_tail=self._tail("".join(stderr_chunks)),
                        host=self.host,
                        elapsed_seconds=round(elapsed, 1),
                    )
                    last_report = now
                    last_reported_size = total_size

                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(0.1)

            exit_code = channel.recv_exit_status()
            stdout_text = "".join(stdout_chunks)
            stderr_text = "".join(stderr_chunks)
            increment(
                "agent_tool_results",
                labels={"transport": "ssh", "status": "success" if exit_code == 0 else "failed"},
            )
            report_progress(
                "command_completed",
                status="completed" if exit_code == 0 else "failed",
                detail=f"Comando finalizado com código {exit_code}: {safe_command}",
                command_id=command_id,
                command=safe_command,
                exit_code=exit_code,
                stdout_tail=self._tail(stdout_text),
                stderr_tail=self._tail(stderr_text),
                host=self.host,
                elapsed_seconds=round(time.monotonic() - started, 1),
            )
            return CommandResult(command, exit_code, stdout_text, stderr_text)
        except ExecutionCancelled:
            channel.close()
            increment("agent_tool_results", labels={"transport": "ssh", "status": "cancelled"})
            report_progress(
                "command_cancelled",
                status="cancelled",
                detail=f"Comando interrompido pelo operador: {safe_command}",
                command_id=command_id,
                command=safe_command,
                stdout_tail=self._tail("".join(stdout_chunks)),
                stderr_tail=self._tail("".join(stderr_chunks)),
                host=self.host,
                elapsed_seconds=round(time.monotonic() - started, 1),
            )
            raise
        except Exception:
            increment("agent_tool_results", labels={"transport": "ssh", "status": "exception"})
            raise
        finally:
            observe(
                "agent_tool_duration_seconds",
                time.monotonic() - started,
                labels={"transport": "ssh"},
            )

    def run(self, command: str, environment: EnvironmentType, approved: bool = False, timeout: int = 60) -> CommandResult:
        if not self.client:
            raise RuntimeError("Conexão SSH não iniciada.")
        self._validate(command, environment, approved)
        return self._execute_streaming(
            command=command,
            wrapped_command=command,
            timeout=timeout,
        )

    def run_sudo(self, command: str, environment: EnvironmentType, approved: bool = False, timeout: int = 60) -> CommandResult:
        if not self.client:
            raise RuntimeError("Conexão SSH não iniciada.")
        self._validate(command, environment, approved)
        if self.password:
            wrapped = f"sudo -S -p '' sh -lc {shlex.quote(command)}"
            password = self.password
        else:
            wrapped = f"sudo -n sh -lc {shlex.quote(command)}"
            password = None
        return self._execute_streaming(
            command=command,
            wrapped_command=wrapped,
            timeout=timeout,
            sudo_password=password,
        )
