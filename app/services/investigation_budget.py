from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Iterator

from app.services.metrics import increment
from app.services.performance_config import PerformanceConfig, get_performance_config
from app.services.progress import report_progress


class InvestigationBudgetExceeded(RuntimeError):
    pass


@dataclass
class InvestigationBudget:
    config: PerformanceConfig
    started_at: float = field(default_factory=time.monotonic)
    commands: int = 0
    ai_calls: int = 0
    deep_dive_hosts: set[str] = field(default_factory=set)
    host_started_at: dict[str, float] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)

    def _elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def _check_global_time(self) -> None:
        elapsed = self._elapsed()
        if elapsed > self.config.max_investigation_seconds:
            increment("agent_budget_exceeded", labels={"kind": "investigation_time"})
            raise InvestigationBudgetExceeded(
                f"a investigação excedeu o limite global de {self.config.max_investigation_seconds}s"
            )

    def reserve_command(self, host: str, timeout: int) -> int:
        normalized_host = str(host or "unknown")
        with self.lock:
            self._check_global_time()
            started = self.host_started_at.setdefault(normalized_host, time.monotonic())
            host_elapsed = time.monotonic() - started
            if host_elapsed > self.config.max_host_seconds:
                increment("agent_budget_exceeded", labels={"kind": "host_time", "host": normalized_host})
                raise InvestigationBudgetExceeded(
                    f"o host {normalized_host} excedeu o limite de {self.config.max_host_seconds}s"
                )
            if self.commands >= self.config.max_total_commands:
                increment("agent_budget_exceeded", labels={"kind": "commands"})
                raise InvestigationBudgetExceeded(
                    f"a investigação atingiu o limite de {self.config.max_total_commands} comandos"
                )
            self.commands += 1
            allowed_timeout = min(
                max(1, int(timeout)),
                max(1, self.config.max_investigation_seconds - int(self._elapsed())),
                max(1, self.config.max_host_seconds - int(host_elapsed)),
            )
            self._report()
            return allowed_timeout

    def reserve_ai_call(self, provider: str) -> None:
        with self.lock:
            self._check_global_time()
            if self.ai_calls >= self.config.max_total_ai_calls:
                increment("agent_budget_exceeded", labels={"kind": "ai_calls"})
                raise InvestigationBudgetExceeded(
                    f"a investigação atingiu o limite de {self.config.max_total_ai_calls} chamadas de IA"
                )
            self.ai_calls += 1
            increment("agent_ai_requests", labels={"provider": provider or "unknown"})
            self._report()

    def allow_deep_dive(self, host: str) -> bool:
        normalized_host = str(host or "unknown")
        with self.lock:
            if normalized_host in self.deep_dive_hosts:
                return True
            if len(self.deep_dive_hosts) >= self.config.max_deep_dive_hosts:
                return False
            self.deep_dive_hosts.add(normalized_host)
            self.host_started_at.setdefault(normalized_host, time.monotonic())
            self._report()
            return True

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "commands": self.commands,
                "commands_limit": self.config.max_total_commands,
                "ai_calls": self.ai_calls,
                "ai_calls_limit": self.config.max_total_ai_calls,
                "deep_dive_hosts": sorted(self.deep_dive_hosts),
                "deep_dive_hosts_limit": self.config.max_deep_dive_hosts,
                "elapsed_seconds": round(self._elapsed(), 1),
                "time_limit_seconds": self.config.max_investigation_seconds,
            }

    def _report(self) -> None:
        report_progress(
            "investigation_budget",
            detail=(
                f"Orçamento: {self.commands}/{self.config.max_total_commands} comandos, "
                f"{self.ai_calls}/{self.config.max_total_ai_calls} chamadas de IA."
            ),
            budget=self.snapshot(),
        )


_CURRENT_BUDGET: ContextVar[InvestigationBudget | None] = ContextVar(
    "agent_investigation_budget",
    default=None,
)


@contextmanager
def use_investigation_budget(
    config: PerformanceConfig | None = None,
) -> Iterator[InvestigationBudget]:
    existing = _CURRENT_BUDGET.get()
    if existing is not None:
        yield existing
        return
    budget = InvestigationBudget(config=config or get_performance_config())
    token = _CURRENT_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_BUDGET.reset(token)


def current_budget() -> InvestigationBudget | None:
    return _CURRENT_BUDGET.get()


def reserve_command(host: str, timeout: int) -> int:
    budget = current_budget()
    return budget.reserve_command(host, timeout) if budget else timeout


def reserve_ai_call(provider: str) -> None:
    budget = current_budget()
    if budget:
        budget.reserve_ai_call(provider)


def allow_deep_dive(host: str) -> bool:
    budget = current_budget()
    return budget.allow_deep_dive(host) if budget else True


def budget_snapshot() -> dict | None:
    budget = current_budget()
    return budget.snapshot() if budget else None
