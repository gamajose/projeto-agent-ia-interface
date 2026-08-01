from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "sim"}


def _int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class PerformanceConfig:
    execution_store_enabled: bool
    execution_ttl_seconds: int
    execution_event_maxlen: int
    execution_memory_max_records: int
    execution_thread_workers: int
    sse_enabled: bool
    sse_heartbeat_seconds: int
    sse_block_milliseconds: int
    max_total_commands: int
    max_total_ai_calls: int
    max_investigation_seconds: int
    max_host_seconds: int
    max_deep_dive_hosts: int
    triage_enabled: bool
    triage_timeout_seconds: int
    cache_enabled: bool
    runtime_cache_seconds: int
    topology_cache_seconds: int
    provider_cache_seconds: int
    metrics_enabled: bool
    replay_enabled: bool
    nested_ssh_control_persist_seconds: int
    nested_ssh_master_enabled: bool
    quick_poll_seconds: float


def get_performance_config() -> PerformanceConfig:
    return PerformanceConfig(
        execution_store_enabled=_bool("AGENT_EXECUTION_STORE_ENABLED", True),
        execution_ttl_seconds=_int("AGENT_EXECUTION_TTL_SECONDS", 86400, minimum=300, maximum=604800),
        execution_event_maxlen=_int("AGENT_EXECUTION_EVENT_MAXLEN", 1000, minimum=100, maximum=10000),
        execution_memory_max_records=_int("AGENT_EXECUTION_MEMORY_MAX_RECORDS", 500, minimum=50, maximum=5000),
        execution_thread_workers=_int("AGENT_UI_EXECUTION_WORKERS", 8, minimum=1, maximum=32),
        sse_enabled=_bool("AGENT_SSE_ENABLED", True),
        sse_heartbeat_seconds=_int("AGENT_SSE_HEARTBEAT_SECONDS", 15, minimum=5, maximum=60),
        sse_block_milliseconds=_int("AGENT_SSE_BLOCK_MILLISECONDS", 12000, minimum=1000, maximum=60000),
        max_total_commands=_int("AGENT_MAX_TOTAL_COMMANDS", 30, minimum=5, maximum=200),
        max_total_ai_calls=_int("AGENT_MAX_TOTAL_AI_CALLS", 12, minimum=1, maximum=100),
        max_investigation_seconds=_int("AGENT_MAX_INVESTIGATION_SECONDS", 900, minimum=60, maximum=7200),
        max_host_seconds=_int("AGENT_MAX_HOST_SECONDS", 300, minimum=30, maximum=3600),
        max_deep_dive_hosts=_int("AGENT_MAX_DEEP_DIVE_HOSTS", 2, minimum=1, maximum=5),
        triage_enabled=_bool("AGENT_MULTI_HOST_TRIAGE_ENABLED", True),
        triage_timeout_seconds=_int("AGENT_MULTI_HOST_TRIAGE_TIMEOUT_SECONDS", 45, minimum=10, maximum=180),
        cache_enabled=_bool("AGENT_RUNTIME_CACHE_ENABLED", True),
        runtime_cache_seconds=_int("AGENT_RUNTIME_CACHE_SECONDS", 120, minimum=0, maximum=1800),
        topology_cache_seconds=_int("AGENT_TOPOLOGY_CACHE_SECONDS", 300, minimum=0, maximum=3600),
        provider_cache_seconds=_int("AGENT_PROVIDER_CACHE_SECONDS", 60, minimum=0, maximum=600),
        metrics_enabled=_bool("AGENT_METRICS_ENABLED", True),
        replay_enabled=_bool("AGENT_REPLAY_ENABLED", True),
        nested_ssh_control_persist_seconds=_int(
            "AGENT_NESTED_SSH_CONTROL_PERSIST_SECONDS", 300, minimum=30, maximum=1800
        ),
        nested_ssh_master_enabled=_bool("AGENT_NESTED_SSH_MASTER_ENABLED", True),
        quick_poll_seconds=_float("AGENT_UI_POLL_SECONDS", 1.2, minimum=0.5, maximum=10.0),
    )
