from __future__ import annotations

import re
import shlex
import time
from pathlib import PurePath
from uuid import uuid4

import paramiko

from app.services.cancellation import raise_if_cancelled
from app.services.redaction import redact_text
from app.services.vpn_menu import strip_terminal_codes
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


_HOST_KEY_PROMPT = re.compile(r"Are you sure you want to continue connecting", re.I)
_PASSWORD_PROMPT = re.compile(
    r"(?:(?P<user>[A-Za-z0-9._-]+)@[^\r\n:]+(?:'s|’s)\s+password|password)\s*:\s*$",
    re.I | re.M,
)
_PERMISSION_DENIED = re.compile(r"Permission denied|Authentication failed", re.I)
_CONNECTION_FAILURE = re.compile(
    r"No route to host|Connection timed out|Connection refused|Could not resolve hostname|"
    r"Host key verification failed|Connection closed|Connection reset|kex_exchange_identification",
    re.I,
)
_PATCH_MARKER = "_monitor_direct_ssh_installed"


def _render_command(executor: VPNMenuSSHExecutor) -> str:
    values = {
        "host": shlex.quote(str(executor.host)),
        "port": int(executor.port),
        "user": shlex.quote(str(executor.username)),
    }
    try:
        return str(executor.vpn_command).format(**values).strip()
    except KeyError as exc:
        raise ValueError(f"placeholder desconhecido em SSH_VPN_COMMAND: {exc.args[0]}") from exc


def _is_ssh_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    return PurePath(parts[0]).name == "ssh"


def _tail(value: str) -> str:
    return redact_text(strip_terminal_codes(value)[-1200:])


def _raise_terminal_failure(clean: str) -> None:
    if _PERMISSION_DENIED.search(clean):
        raise PermissionError("o SSH do servidor cliente recusou a credencial de SSH_DEFAULT_PASSWORD")
    match = _CONNECTION_FAILURE.search(clean)
    if match:
        raise ConnectionError(f"o SSH do servidor cliente falhou: {match.group(0)}")


def _wait_for_password_prompt(executor: VPNMenuSSHExecutor) -> tuple[str, str]:
    channel = executor._channel()
    deadline = time.monotonic() + executor.vpn_menu_timeout
    buffer = ""
    host_key_confirmed = False

    while time.monotonic() < deadline:
        raise_if_cancelled("Acesso cancelado durante a abertura do SSH do servidor cliente.")
        while channel.recv_ready():
            buffer += channel.recv(32768).decode(errors="replace")
        clean = strip_terminal_codes(buffer)
        _raise_terminal_failure(clean)

        if _HOST_KEY_PROMPT.search(clean) and not host_key_confirmed:
            executor._send_line("yes")
            host_key_confirmed = True
            buffer = ""
            continue

        match = _PASSWORD_PROMPT.search(clean)
        if match:
            return str(match.group("user") or executor.username).strip(), buffer
        if channel.closed:
            break
        time.sleep(0.1)

    raise TimeoutError(
        "o comando ssh não solicitou a senha do servidor cliente dentro do prazo. "
        f"Última saída: {_tail(buffer) or 'sem saída'}"
    )


def _probe_target_shell(executor: VPNMenuSSHExecutor) -> None:
    channel = executor._channel()
    token = uuid4().hex
    marker = f"__AGENT_READY_{token}__"
    executor._send_line(f"printf '\\n__AGENT_READY_%s__\\n' {shlex.quote(token)}")

    deadline = time.monotonic() + executor.vpn_menu_timeout
    buffer = ""
    while time.monotonic() < deadline:
        raise_if_cancelled("Acesso cancelado durante a validação do shell do servidor cliente.")
        while channel.recv_ready():
            buffer += channel.recv(32768).decode(errors="replace")
        clean = strip_terminal_codes(buffer)
        _raise_terminal_failure(clean)

        if _PASSWORD_PROMPT.search(clean):
            raise PermissionError(
                "o SSH do servidor cliente voltou a solicitar senha; valide SSH_DEFAULT_PASSWORD"
            )
        if marker in clean:
            executor._send_line("stty -echo 2>/dev/null || true")
            executor._drain(quiet_seconds=0.2, limit_seconds=1.0)
            return
        if channel.closed:
            break
        time.sleep(0.1)

    raise TimeoutError(
        "a autenticação foi iniciada, mas o shell remoto não respondeu ao teste de prontidão. "
        f"Última saída: {_tail(buffer) or 'sem saída'}"
    )


def _connect_direct_ssh(executor: VPNMenuSSHExecutor, command: str) -> None:
    raise_if_cancelled("Conexão SSH cancelada pelo operador.")
    executor.access_journey.clear()
    executor.connection_metadata = {
        "mode": "ssh_via_monitor",
        "bastion_host": executor.bastion_host,
        "vpn_ip": executor.host,
        "ssh_port": executor.port,
        "username": executor.username,
        "access_journey": executor.access_journey,
    }
    current_step = "bastion"
    current_percent = 32

    try:
        executor._access_progress(
            current_step,
            status="running",
            detail=f"Conectando ao Monitor 1 em {executor.bastion_host}:{executor.bastion_port}.",
            percent=current_percent,
            bastion_host=executor.bastion_host,
        )
        bastion = executor._connect_bastion()
        executor.interactive_channel = bastion.invoke_shell(term="xterm", width=160, height=48)
        executor._drain(quiet_seconds=0.25, limit_seconds=2.0)
        executor._access_progress(
            current_step,
            status="completed",
            detail="Monitor 1 autenticado e terminal interativo aberto.",
            percent=35,
            bastion_host=executor.bastion_host,
        )

        current_step = "authentication"
        current_percent = 38
        executor._access_progress(
            current_step,
            status="running",
            detail=f"Executando {redact_text(command)} no Monitor 1 e aguardando a senha do alvo.",
            percent=current_percent,
            vpn_ip=executor.host,
            ssh_port=executor.port,
            username=executor.username,
        )
        executor._send_line(command)
        prompted_user, _ = _wait_for_password_prompt(executor)
        if not executor.password:
            raise ValueError("SSH_DEFAULT_PASSWORD não está configurada para autenticar no alvo")
        executor.remote_username = prompted_user
        executor._send_line(executor.password)

        current_step = "target_shell"
        current_percent = 44
        executor._access_progress(
            current_step,
            status="running",
            detail="Senha enviada. Validando se o shell do alvo aceita comandos de coleta.",
            percent=current_percent,
            vpn_ip=executor.host,
            ssh_port=executor.port,
            username=prompted_user,
        )
        _probe_target_shell(executor)
        executor._access_progress(
            "authentication",
            status="completed",
            detail=f"Autenticação SSH aceita para o usuário {prompted_user}.",
            percent=45,
            vpn_ip=executor.host,
            username=prompted_user,
        )
        executor._access_progress(
            current_step,
            status="completed",
            detail=f"Shell de {executor.host}:{executor.port} validado e pronto para a investigação.",
            percent=46,
            vpn_ip=executor.host,
            ssh_port=executor.port,
            username=prompted_user,
        )
        executor.connection_metadata.update(
            {
                "mode": "ssh_via_monitor",
                "bastion_host": executor.bastion_host,
                "vpn_ip": executor.host,
                "ssh_port": executor.port,
                "username": prompted_user,
                "is_pfsense": False,
                "access_journey": [dict(item) for item in executor.access_journey],
            }
        )
    except Exception as exc:
        executor._access_progress(
            current_step,
            status="failed",
            detail=redact_text(f"Falha no acesso SSH ao alvo: {type(exc).__name__}: {exc}"),
            percent=current_percent,
            vpn_ip=executor.host,
            ssh_port=executor.port,
        )
        raise


def install_monitor_direct_ssh() -> None:
    """Permite usar SSH direto dentro do Monitor 1 sem remover o menu legado.

    Quando ``SSH_VPN_COMMAND`` começa com ``ssh``, o executor ignora o inventário
    ``vpn``, não envia índice nem ``y`` e abre diretamente ``SSH_DEFAULT_USER`` no
    endereço solicitado. Outros comandos continuam usando o fluxo legado.
    """

    if getattr(VPNMenuSSHExecutor, _PATCH_MARKER, False):
        return

    original_connect = VPNMenuSSHExecutor.connect

    def connect(executor: VPNMenuSSHExecutor) -> None:
        command = _render_command(executor)
        if _is_ssh_command(command):
            _connect_direct_ssh(executor, command)
            return
        original_connect(executor)

    VPNMenuSSHExecutor.connect = connect
    setattr(VPNMenuSSHExecutor, _PATCH_MARKER, True)


install_monitor_direct_ssh()
