from __future__ import annotations

import re
import shlex
import time
from typing import Pattern
from uuid import uuid4

import paramiko

from app.core.policies import EnvironmentType
from app.services.cancellation import ExecutionCancelled, raise_if_cancelled
from app.services.progress import report_progress
from app.services.redaction import redact_text
from app.services.ssh import CommandResult, SSHExecutor
from app.services.vpn_menu import VPNMenuEntry, select_vpn_menu_entry, strip_terminal_codes


_PASSWORD_PROMPT = re.compile(r"Enter\s+SSH\s+password\s+for\s+User\s+(?P<user>[^:\r\n]+)\s*:", re.I)
_HOST_KEY_PROMPT = re.compile(r"Are you sure you want to continue connecting", re.I)
_PERMISSION_DENIED = re.compile(r"Permission denied|Authentication failed", re.I)
_PFSENSE_MENU_PROMPT = re.compile(r"Enter an option\s*:", re.I)
_SELECTION_PROMPT = re.compile(r"Qual o N[uú]mero do Servidor Para Acesso", re.I)
_CONFIRMATION_PROMPT = re.compile(r"Deseja Acessar o Servidor|\[Y\|N]", re.I)
_ACCESS_LABELS = {
    "bastion": "Monitor 1",
    "inventory": "Inventário VPN",
    "selection": "Seleção da linha",
    "confirmation": "Confirmação de acesso",
    "authentication": "Autenticação no destino",
    "pfsense_shell": "Shell do pfSense",
    "target_shell": "Shell do alvo",
}


class VPNMenuSSHExecutor(SSHExecutor):
    """Executa comandos no alvo após navegar pelo menu ``vpn IP`` do bastion.

    O servidor de VPN não funciona como um jump host TCP convencional. Ele abre
    uma sessão SSH aninhada depois que o operador seleciona a linha do cliente,
    confirma o acesso e informa a senha. Esta classe reproduz esse fluxo em um
    canal de terminal sem registrar credenciais.
    """

    def __init__(
        self,
        *args,
        vpn_command: str = "vpn {host}",
        vpn_menu_timeout: int = 45,
        firewall_user: str = "root",
        firewall_password: str | None = None,
        firewall_port: int = 2224,
        firewall_shell_option: int = 8,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.vpn_command = vpn_command
        self.vpn_menu_timeout = max(10, int(vpn_menu_timeout))
        self.firewall_user = firewall_user
        self.firewall_password = firewall_password
        self.firewall_port = int(firewall_port)
        self.firewall_shell_option = int(firewall_shell_option)
        self.interactive_channel: paramiko.Channel | None = None
        self.remote_username = self.username
        self.access_journey: list[dict[str, object]] = []
        self.connection_metadata: dict[str, object] = {
            "mode": "vpn_menu",
            "access_journey": self.access_journey,
        }

    def _channel(self) -> paramiko.Channel:
        if self.interactive_channel is None or self.interactive_channel.closed:
            raise RuntimeError("Sessão interativa da VPN não está ativa.")
        return self.interactive_channel

    @staticmethod
    def _matches(value: str, patterns: tuple[Pattern[str], ...]) -> bool:
        return any(pattern.search(value) for pattern in patterns)

    def _access_progress(
        self,
        step: str,
        *,
        status: str,
        detail: str,
        percent: int,
        **metadata: object,
    ) -> None:
        item = {
            "step": step,
            "label": _ACCESS_LABELS.get(step, step.replace("_", " ").title()),
            "status": status,
            "detail": detail,
            **metadata,
        }
        index = next(
            (position for position, current in enumerate(self.access_journey) if current.get("step") == step),
            None,
        )
        if index is None:
            self.access_journey.append(item)
        else:
            self.access_journey[index] = {**self.access_journey[index], **item}
        self.connection_metadata["access_journey"] = [dict(current) for current in self.access_journey]
        report_progress(
            "ssh_connection",
            status=status,
            detail=detail,
            access_step=step,
            access_label=item["label"],
            access_journey=[dict(current) for current in self.access_journey],
            percent=percent,
            **metadata,
        )

    def _receive_until(
        self,
        patterns: tuple[Pattern[str], ...],
        *,
        timeout: int | float | None = None,
        initial: str = "",
        purpose: str,
    ) -> str:
        channel = self._channel()
        deadline = time.monotonic() + float(timeout or self.vpn_menu_timeout)
        buffer = initial
        while time.monotonic() < deadline:
            raise_if_cancelled(f"Acesso VPN cancelado durante: {purpose}.")
            changed = False
            while channel.recv_ready():
                buffer += channel.recv(32768).decode(errors="replace")
                changed = True
            clean = strip_terminal_codes(buffer)
            if self._matches(clean, patterns):
                return buffer
            if channel.closed:
                break
            time.sleep(0.08 if changed else 0.15)
        tail = redact_text(strip_terminal_codes(buffer)[-1200:])
        raise TimeoutError(f"timeout aguardando {purpose}. Última saída: {tail or 'sem saída'}")

    def _drain(self, *, quiet_seconds: float = 0.25, limit_seconds: float = 2.0) -> str:
        channel = self._channel()
        started = time.monotonic()
        last_data = started
        chunks: list[str] = []
        while time.monotonic() - started < limit_seconds:
            changed = False
            while channel.recv_ready():
                chunks.append(channel.recv(32768).decode(errors="replace"))
                last_data = time.monotonic()
                changed = True
            if not changed and time.monotonic() - last_data >= quiet_seconds:
                break
            time.sleep(0.05)
        return "".join(chunks)

    def _send_line(self, value: str) -> None:
        self._channel().send(str(value) + "\n")

    def _wait_for_password_prompt(self) -> tuple[str, str]:
        channel = self._channel()
        deadline = time.monotonic() + self.vpn_menu_timeout
        buffer = ""
        host_key_confirmed = False
        while time.monotonic() < deadline:
            raise_if_cancelled("Acesso VPN cancelado durante a autenticação do cliente.")
            while channel.recv_ready():
                buffer += channel.recv(32768).decode(errors="replace")
            clean = strip_terminal_codes(buffer)
            if _PERMISSION_DENIED.search(clean):
                raise PermissionError("o menu VPN recusou a autenticação no servidor cliente")
            if _HOST_KEY_PROMPT.search(clean) and not host_key_confirmed:
                self._send_line("yes")
                host_key_confirmed = True
                buffer = ""
                continue
            match = _PASSWORD_PROMPT.search(clean)
            if match:
                return match.group("user").strip(), buffer
            if channel.closed:
                break
            time.sleep(0.1)
        tail = redact_text(strip_terminal_codes(buffer)[-1200:])
        raise TimeoutError(
            "o menu VPN não solicitou a senha do servidor cliente dentro do prazo. "
            f"Última saída: {tail or 'sem saída'}"
        )

    def _probe_target_shell(self) -> None:
        token = uuid4().hex
        marker = f"__AGENT_READY_{token}__"
        command = f"printf '\\n__AGENT_READY_%s__\\n' {shlex.quote(token)}"
        self._send_line(command)
        marker_pattern = re.compile(re.escape(marker))
        self._receive_until(
            (marker_pattern,),
            timeout=self.vpn_menu_timeout,
            purpose="confirmação do shell remoto",
        )
        self._send_line("stty -echo 2>/dev/null || true")
        self._drain(quiet_seconds=0.2, limit_seconds=1.0)

    def _connect_bastion(self) -> paramiko.SSHClient:
        if not self.bastion_host:
            raise ValueError("SSH_SRV_VPN_IP/SSH_BASTION_HOST não está configurado")
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
        self.bastion_client = bastion
        return bastion

    def connect(self) -> None:
        raise_if_cancelled("Conexão SSH cancelada pelo operador.")
        current_step = "bastion"
        current_percent = 32
        try:
            self._access_progress(
                current_step,
                status="running",
                detail=f"Conectando ao Monitor 1 em {self.bastion_host}:{self.bastion_port}.",
                percent=current_percent,
                bastion_host=self.bastion_host,
            )
            bastion = self._connect_bastion()
            channel = bastion.invoke_shell(term="xterm", width=160, height=48)
            self.interactive_channel = channel
            self._drain(quiet_seconds=0.25, limit_seconds=2.0)
            self._access_progress(
                current_step,
                status="completed",
                detail="Monitor 1 autenticado e terminal interativo aberto.",
                percent=34,
                bastion_host=self.bastion_host,
            )

            current_step = "inventory"
            current_percent = 35
            command = self.vpn_command.format(host=self.host)
            self._access_progress(
                current_step,
                status="running",
                detail=f"Executando {redact_text(command)} e aguardando o inventário VPN.",
                percent=current_percent,
                vpn_ip=self.host,
            )
            self._send_line(command)
            menu_output = self._receive_until(
                (_SELECTION_PROMPT,),
                purpose="inventário e seleção do cliente no menu VPN",
            )
            entry = select_vpn_menu_entry(
                menu_output,
                self.host,
                default_port=self.port,
                pfsense_port=self.firewall_port,
            )
            self._access_progress(
                current_step,
                status="completed",
                detail=f"Cliente identificado: {entry.display_name} ({entry.vpn_ip}).",
                percent=37,
                client_name=entry.display_name,
                vpn_ip=entry.vpn_ip,
                vpn_index=entry.index,
            )

            current_step = "selection"
            current_percent = 38
            self._access_progress(
                current_step,
                status="running",
                detail=f"Selecionando a linha {entry.index} do cliente {entry.display_name}.",
                percent=current_percent,
                client_name=entry.display_name,
                vpn_index=entry.index,
            )
            self._send_line(str(entry.index))
            self._receive_until(
                (_CONFIRMATION_PROMPT,),
                purpose="confirmação de acesso ao cliente",
            )
            self._access_progress(
                current_step,
                status="completed",
                detail=f"Linha {entry.index} selecionada e confirmação solicitada.",
                percent=39,
                client_name=entry.display_name,
                vpn_index=entry.index,
            )

            current_step = "confirmation"
            current_percent = 40
            self._access_progress(
                current_step,
                status="running",
                detail="Confirmando o acesso ao servidor cliente com y.",
                percent=current_percent,
            )
            self._send_line("y")
            self._access_progress(
                current_step,
                status="completed",
                detail="Acesso confirmado no menu VPN.",
                percent=41,
            )

            current_step = "authentication"
            current_percent = 42
            self._access_progress(
                current_step,
                status="running",
                detail="Aguardando o prompt de senha do servidor cliente.",
                percent=current_percent,
                client_name=entry.display_name,
            )
            prompted_user, _ = self._wait_for_password_prompt()
            use_firewall_credentials = entry.is_pfsense or prompted_user == self.firewall_user
            password = self.firewall_password if use_firewall_credentials else self.password
            if not password:
                variable = "SSH_FIREWALL_PF_PASSWORD" if use_firewall_credentials else "SSH_DEFAULT_PASSWORD"
                raise ValueError(f"{variable} não está configurada para autenticar no alvo")
            self.remote_username = prompted_user
            self._send_line(password)
            self._access_progress(
                current_step,
                status="completed",
                detail=f"Credencial enviada para o usuário {prompted_user}; aguardando o shell remoto.",
                percent=43,
                username=prompted_user,
                is_pfsense=bool(use_firewall_credentials),
            )

            if use_firewall_credentials:
                current_step = "pfsense_shell"
                current_percent = 44
                self._access_progress(
                    current_step,
                    status="running",
                    detail="Aguardando o menu do pfSense para selecionar a opção 8.",
                    percent=current_percent,
                    shell_option=self.firewall_shell_option,
                )
                self._receive_until(
                    (_PFSENSE_MENU_PROMPT,),
                    purpose="menu do pfSense",
                )
                self._send_line(str(self.firewall_shell_option))
                self._access_progress(
                    current_step,
                    status="completed",
                    detail=f"Opção {self.firewall_shell_option} selecionada no pfSense.",
                    percent=45,
                    shell_option=self.firewall_shell_option,
                )

            current_step = "target_shell"
            current_percent = 45
            self._access_progress(
                current_step,
                status="running",
                detail="Validando se o shell do alvo aceita comandos de coleta.",
                percent=current_percent,
                client_name=entry.display_name,
            )
            self._probe_target_shell()
            self.port = entry.ssh_port
            self._access_progress(
                current_step,
                status="completed",
                detail=f"Shell de {entry.display_name} validado e pronto para a investigação.",
                percent=46,
                client_name=entry.display_name,
                ssh_port=entry.ssh_port,
            )
            self.connection_metadata.update(
                {
                    "mode": "vpn_menu",
                    "bastion_host": self.bastion_host,
                    "vpn_index": entry.index,
                    "vpn_ip": entry.vpn_ip,
                    "client_name": entry.display_name,
                    "raw_client_name": entry.raw_name,
                    "client_code": entry.client_code,
                    "port_spec": entry.port_spec,
                    "ssh_port": entry.ssh_port,
                    "username": prompted_user,
                    "is_pfsense": bool(use_firewall_credentials),
                    "access_journey": [dict(item) for item in self.access_journey],
                }
            )
        except Exception as exc:
            detail = f"Falha em {_ACCESS_LABELS.get(current_step, current_step)}: {type(exc).__name__}: {exc}"
            self._access_progress(
                current_step,
                status="failed",
                detail=redact_text(detail),
                percent=current_percent,
                vpn_ip=self.host,
            )
            raise

    def close(self) -> None:
        if self.interactive_channel is not None:
            try:
                self.interactive_channel.close()
            finally:
                self.interactive_channel = None
        super().close()

    def _execute_interactive(
        self,
        *,
        command: str,
        timeout: int,
        sudo_password: str | None = None,
    ) -> CommandResult:
        channel = self._channel()
        command_id = str(uuid4())
        token = uuid4().hex
        start_marker = f"__AGENT_START_{token}__"
        done_marker = f"__AGENT_DONE_{token}__"
        sudo_marker = f"__AGENT_SUDO_{token}__"
        safe_command = redact_text(command)
        started = time.monotonic()
        report_progress(
            "command_started",
            detail=f"Executando: {safe_command}",
            command_id=command_id,
            command=safe_command,
            host=self.host,
            ssh_port=self.port,
        )

        self._drain(quiet_seconds=0.05, limit_seconds=0.3)
        if sudo_password is not None:
            remote = f"sudo -S -p {shlex.quote(sudo_marker)} sh -lc {shlex.quote(command)}"
        else:
            remote = command
        payload = (
            f"printf '\\n__AGENT_START_%s__\\n' {shlex.quote(token)}; "
            f"{remote}; __agent_rc=$?; "
            f"printf '\\n__AGENT_DONE_%s__:%s\\n' {shlex.quote(token)} \"$__agent_rc\""
        )
        self._send_line(payload)

        raw = ""
        password_sent = False
        last_report = 0.0
        deadline = started + max(1, int(timeout))
        done_pattern = re.compile(re.escape(done_marker) + r":(?P<code>-?\d+)")
        try:
            while time.monotonic() < deadline:
                raise_if_cancelled(f"Coleta cancelada durante o comando: {safe_command}")
                changed = False
                while channel.recv_ready():
                    raw += channel.recv(32768).decode(errors="replace")
                    changed = True
                clean = strip_terminal_codes(raw)
                if sudo_password is not None and sudo_marker in clean and not password_sent:
                    self._send_line(sudo_password)
                    password_sent = True
                match = done_pattern.search(clean)
                if match:
                    start_at = clean.find(start_marker)
                    body_start = start_at + len(start_marker) if start_at >= 0 else 0
                    body = clean[body_start:match.start()]
                    body = body.replace(sudo_marker, "").strip("\n")
                    exit_code = int(match.group("code"))
                    report_progress(
                        "command_completed",
                        status="completed" if exit_code == 0 else "failed",
                        detail=f"Comando finalizado com código {exit_code}: {safe_command}",
                        command_id=command_id,
                        command=safe_command,
                        exit_code=exit_code,
                        stdout_tail=self._tail(body),
                        stderr_tail="",
                        elapsed_seconds=round(time.monotonic() - started, 1),
                    )
                    return CommandResult(command, exit_code, body, "")
                now = time.monotonic()
                if changed and now - last_report >= 0.45:
                    visible = clean
                    start_at = visible.find(start_marker)
                    if start_at >= 0:
                        visible = visible[start_at + len(start_marker):]
                    visible = visible.replace(sudo_marker, "")
                    report_progress(
                        "command_output",
                        detail=f"Recebendo saída de: {safe_command}",
                        command_id=command_id,
                        command=safe_command,
                        stdout_tail=self._tail(visible),
                        stderr_tail="",
                        elapsed_seconds=round(now - started, 1),
                    )
                    last_report = now
                if channel.closed:
                    raise paramiko.SSHException("o canal do menu VPN foi encerrado durante a coleta")
                time.sleep(0.08 if changed else 0.12)
            raise TimeoutError(f"comando excedeu o timeout de {timeout}s: {safe_command}")
        except ExecutionCancelled:
            channel.send("\x03")
            report_progress(
                "command_cancelled",
                status="cancelled",
                detail=f"Comando interrompido pelo operador: {safe_command}",
                command_id=command_id,
                command=safe_command,
                stdout_tail=self._tail(strip_terminal_codes(raw).replace(sudo_marker, "")),
                stderr_tail="",
                elapsed_seconds=round(time.monotonic() - started, 1),
            )
            raise

    def run(
        self,
        command: str,
        environment: EnvironmentType,
        approved: bool = False,
        timeout: int = 60,
    ) -> CommandResult:
        self._validate(command, environment, approved)
        return self._execute_interactive(command=command, timeout=timeout)

    def run_sudo(
        self,
        command: str,
        environment: EnvironmentType,
        approved: bool = False,
        timeout: int = 60,
    ) -> CommandResult:
        self._validate(command, environment, approved)
        if self.remote_username == "root" or self.remote_username == self.firewall_user:
            return self._execute_interactive(command=command, timeout=timeout)
        if self.password:
            return self._execute_interactive(
                command=command,
                timeout=timeout,
                sudo_password=self.password,
            )
        return self._execute_interactive(command=f"sudo -n sh -lc {shlex.quote(command)}", timeout=timeout)


def connection_display_name(executor: SSHExecutor) -> str | None:
    metadata = getattr(executor, "connection_metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = str(metadata.get("client_name") or "").strip()
    return value or None


def connection_entry(executor: SSHExecutor) -> VPNMenuEntry | None:
    metadata = getattr(executor, "connection_metadata", None)
    if not isinstance(metadata, dict) or metadata.get("mode") != "vpn_menu":
        return None
    try:
        return VPNMenuEntry(
            index=int(metadata["vpn_index"]),
            vpn_ip=str(metadata["vpn_ip"]),
            raw_name=str(metadata["raw_client_name"]),
            display_name=str(metadata["client_name"]),
            port_spec=str(metadata["port_spec"]),
            client_code=str(metadata["client_code"]),
            ssh_port=int(metadata["ssh_port"]),
            is_pfsense=bool(metadata["is_pfsense"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
