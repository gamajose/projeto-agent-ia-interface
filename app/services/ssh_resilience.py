from __future__ import annotations

import time
from typing import Any, Callable

import paramiko

from app.services.ssh import SSHExecutor


_INSTALLED = False
_ORIGINAL_CONNECT: Callable[..., Any] | None = None
_ORIGINAL_COMMON_ARGS: Callable[..., dict[str, Any]] | None = None


def _retryable_banner_error(exc: BaseException) -> bool:
    if isinstance(exc, EOFError):
        return True
    if not isinstance(exc, paramiko.SSHException):
        return False
    message = str(exc).casefold()
    return (
        "protocol banner" in message
        or "error reading ssh" in message
        or "banner" in message and "ssh" in message
    )


def install_ssh_resilience() -> None:
    """Fortalece o handshake SSH sem mascarar falhas de autenticação.

    O OpenSSH costuma tolerar melhor servidores que demoram alguns segundos para
    entregar o banner. O Agent usa Paramiko, então aumentamos somente a janela
    do banner/autenticação e repetimos falhas transitórias de banner/EOF. Erros
    de senha, host key, permissão ou rota continuam falhando imediatamente.
    """

    global _INSTALLED, _ORIGINAL_CONNECT, _ORIGINAL_COMMON_ARGS
    if _INSTALLED:
        return

    _ORIGINAL_CONNECT = SSHExecutor.connect
    _ORIGINAL_COMMON_ARGS = SSHExecutor._common_connect_args

    original_connect = _ORIGINAL_CONNECT
    original_common_args = _ORIGINAL_COMMON_ARGS

    def resilient_common_args(self: SSHExecutor) -> dict[str, Any]:
        args = dict(original_common_args(self))
        args["banner_timeout"] = max(45, int(args.get("banner_timeout") or 0))
        args["auth_timeout"] = max(30, int(args.get("auth_timeout") or 0))
        return args

    def resilient_connect(self: SSHExecutor) -> None:
        delays = (0.0, 1.25, 3.0)
        last_error: BaseException | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                time.sleep(delay)
            try:
                original_connect(self)
                return
            except BaseException as exc:
                last_error = exc
                if not _retryable_banner_error(exc) or attempt >= len(delays):
                    raise
                self.close()
        if last_error is not None:
            raise last_error

    SSHExecutor._common_connect_args = resilient_common_args
    SSHExecutor.connect = resilient_connect
    _INSTALLED = True
