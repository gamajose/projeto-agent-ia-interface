from __future__ import annotations

import json
import time
from collections import OrderedDict, defaultdict
from copy import deepcopy
from threading import Condition, RLock
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.settings import get_settings
from app.services.performance_config import get_performance_config


class ExecutionStore:
    """Armazena o estado curto das execuções e publica eventos para SSE.

    O Redis é usado quando disponível. Em WSL ou instalações simples, a classe
    mantém compatibilidade com o armazenamento em memória sem impedir o uso da
    interface.
    """

    def __init__(self) -> None:
        config = get_performance_config()
        self.ttl_seconds = config.execution_ttl_seconds
        self.event_maxlen = config.execution_event_maxlen
        self.memory_max_records = config.execution_memory_max_records
        self.enabled = config.execution_store_enabled
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._events: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        self._sequence: dict[str, int] = defaultdict(int)
        self._redis: Redis | None = None
        self._redis_checked = False
        self._redis_disabled_until = 0.0

    @staticmethod
    def _record_key(execution_id: str) -> str:
        return f"agent-ia:ui-execution:{execution_id}"

    @staticmethod
    def _stream_key(execution_id: str) -> str:
        return f"agent-ia:ui-execution:{execution_id}:events"

    def _client(self) -> Redis | None:
        if not self.enabled:
            return None
        now = time.monotonic()
        if now < self._redis_disabled_until:
            return None
        if self._redis is None:
            settings = get_settings()
            self._redis = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=2.0,
                health_check_interval=30,
            )
        if not self._redis_checked:
            try:
                self._redis.ping()
                self._redis_checked = True
            except RedisError:
                self._redis_disabled_until = now + 30.0
                return None
        return self._redis

    def _redis_failed(self) -> None:
        self._redis_checked = False
        self._redis_disabled_until = time.monotonic() + 15.0

    def backend_name(self) -> str:
        return "redis" if self._client() is not None else "memory"

    def _prune_memory(self) -> None:
        threshold = time.time() - self.ttl_seconds
        expired = [
            execution_id
            for execution_id, record in self._records.items()
            if float(record.get("_stored_epoch") or 0) < threshold
        ]
        for execution_id in expired:
            self._records.pop(execution_id, None)
            self._events.pop(execution_id, None)
            self._sequence.pop(execution_id, None)
        while len(self._records) > self.memory_max_records:
            execution_id, _record = self._records.popitem(last=False)
            self._events.pop(execution_id, None)
            self._sequence.pop(execution_id, None)

    @staticmethod
    def _serializable(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "_stored_epoch"}

    def save(self, execution_id: str, record: dict[str, Any]) -> None:
        value = deepcopy(record)
        client = self._client()
        if client is not None:
            try:
                client.setex(
                    self._record_key(execution_id),
                    self.ttl_seconds,
                    json.dumps(self._serializable(value), ensure_ascii=False, default=str),
                )
                return
            except RedisError:
                self._redis_failed()
        with self._condition:
            value["_stored_epoch"] = time.time()
            self._records[execution_id] = value
            self._records.move_to_end(execution_id)
            self._prune_memory()
            self._condition.notify_all()

    def get(self, execution_id: str) -> dict[str, Any] | None:
        client = self._client()
        if client is not None:
            try:
                raw = client.get(self._record_key(execution_id))
                if raw:
                    value = json.loads(raw)
                    return value if isinstance(value, dict) else None
            except (RedisError, json.JSONDecodeError):
                self._redis_failed()
        with self._lock:
            self._prune_memory()
            record = self._records.get(execution_id)
            return self._serializable(deepcopy(record)) if record else None

    def append_event(self, execution_id: str, event: dict[str, Any]) -> str:
        payload = deepcopy(event)
        client = self._client()
        if client is not None:
            try:
                cursor = client.xadd(
                    self._stream_key(execution_id),
                    {"payload": json.dumps(payload, ensure_ascii=False, default=str)},
                    maxlen=self.event_maxlen,
                    approximate=True,
                )
                client.expire(self._stream_key(execution_id), self.ttl_seconds)
                return str(cursor)
            except RedisError:
                self._redis_failed()
        with self._condition:
            self._sequence[execution_id] += 1
            sequence = self._sequence[execution_id]
            rows = self._events[execution_id]
            rows.append((sequence, payload))
            if len(rows) > self.event_maxlen:
                del rows[:-self.event_maxlen]
            self._condition.notify_all()
            return str(sequence)

    def latest_cursor(self, execution_id: str) -> str:
        client = self._client()
        if client is not None:
            try:
                rows = client.xrevrange(self._stream_key(execution_id), count=1)
                return str(rows[0][0]) if rows else "0-0"
            except RedisError:
                self._redis_failed()
        with self._lock:
            return str(self._sequence.get(execution_id, 0))

    def read_events(
        self,
        execution_id: str,
        cursor: str | None,
        *,
        block_milliseconds: int,
        count: int = 100,
    ) -> tuple[list[tuple[str, dict[str, Any]]], str]:
        client = self._client()
        if client is not None:
            redis_cursor = cursor if cursor and "-" in cursor else "0-0"
            try:
                batches = client.xread(
                    {self._stream_key(execution_id): redis_cursor},
                    count=max(1, min(500, count)),
                    block=max(1, block_milliseconds),
                )
                output: list[tuple[str, dict[str, Any]]] = []
                latest = redis_cursor
                for _stream, rows in batches:
                    for event_id, fields in rows:
                        latest = str(event_id)
                        try:
                            payload = json.loads(fields.get("payload") or "{}")
                        except json.JSONDecodeError:
                            payload = {"stage": "processing", "detail": "evento inválido descartado"}
                        if isinstance(payload, dict):
                            output.append((latest, payload))
                return output, latest
            except RedisError:
                self._redis_failed()

        try:
            memory_cursor = int(cursor or 0)
        except ValueError:
            memory_cursor = 0
        deadline = time.monotonic() + max(0.01, block_milliseconds / 1000.0)
        with self._condition:
            while True:
                rows = [
                    (str(sequence), deepcopy(payload))
                    for sequence, payload in self._events.get(execution_id, [])
                    if sequence > memory_cursor
                ][: max(1, count)]
                if rows:
                    return rows, rows[-1][0]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], str(memory_cursor)
                self._condition.wait(timeout=remaining)


_STORE = ExecutionStore()


def get_execution_store() -> ExecutionStore:
    return _STORE
