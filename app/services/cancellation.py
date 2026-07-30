from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator


CancellationCallback = Callable[[], bool]
_CANCELLATION_CALLBACK: ContextVar[CancellationCallback | None] = ContextVar(
    "agent_cancellation_callback",
    default=None,
)


class ExecutionCancelled(RuntimeError):
    """Interrompe uma execução acompanhada sem derrubar o processo do Agent."""


def cancellation_requested() -> bool:
    callback = _CANCELLATION_CALLBACK.get()
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def raise_if_cancelled(detail: str = "Coleta cancelada pelo operador.") -> None:
    if cancellation_requested():
        raise ExecutionCancelled(detail)


@contextmanager
def use_cancellation(callback: CancellationCallback | None) -> Iterator[None]:
    token = _CANCELLATION_CALLBACK.set(callback)
    try:
        yield
    finally:
        _CANCELLATION_CALLBACK.reset(token)
