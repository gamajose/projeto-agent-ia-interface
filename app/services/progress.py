from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator


ProgressCallback = Callable[[dict[str, Any]], None]
_PROGRESS_CALLBACK: ContextVar[ProgressCallback | None] = ContextVar(
    "agent_progress_callback",
    default=None,
)


def report_progress(
    stage: str,
    *,
    status: str = "running",
    detail: str = "",
    **metadata: Any,
) -> None:
    callback = _PROGRESS_CALLBACK.get()
    if callback is None:
        return
    callback(
        {
            "stage": str(stage or "processing"),
            "status": str(status or "running"),
            "detail": str(detail or ""),
            **metadata,
        }
    )


@contextmanager
def use_progress(callback: ProgressCallback | None) -> Iterator[None]:
    token = _PROGRESS_CALLBACK.set(callback)
    try:
        yield
    finally:
        _PROGRESS_CALLBACK.reset(token)
