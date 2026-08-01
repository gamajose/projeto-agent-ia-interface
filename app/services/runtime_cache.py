from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from threading import RLock
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.settings import get_settings
from app.services.performance_config import get_performance_config


class RuntimeCache:
    def __init__(self) -> None:
        self.config = get_performance_config()
        self._lock = RLock()
        self._memory: dict[str, tuple[float, Any]] = {}
        self._redis: Redis | None = None
        self._redis_checked = False
        self._redis_disabled_until = 0.0

    def _client(self) -> Redis | None:
        if not self.config.cache_enabled:
            return None
        now = time.monotonic()
        if now < self._redis_disabled_until:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=2.0,
            )
        if not self._redis_checked:
            try:
                self._redis.ping()
                self._redis_checked = True
            except RedisError:
                self._redis_disabled_until = now + 30.0
                return None
        return self._redis

    @staticmethod
    def key(namespace: str, *parts: Any) -> str:
        material = "|".join(str(part or "") for part in parts)
        digest = hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()
        return f"agent-ia:cache:{namespace}:{digest}"

    def get(self, key: str) -> Any | None:
        if not self.config.cache_enabled:
            return None
        client = self._client()
        if client is not None:
            try:
                raw = client.get(key)
                if raw is not None:
                    return json.loads(raw)
            except (RedisError, json.JSONDecodeError):
                self._redis_checked = False
                self._redis_disabled_until = time.monotonic() + 15.0
        with self._lock:
            row = self._memory.get(key)
            if not row:
                return None
            expires_at, value = row
            if expires_at <= time.monotonic():
                self._memory.pop(key, None)
                return None
            return deepcopy(value)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if not self.config.cache_enabled or ttl_seconds <= 0:
            return
        client = self._client()
        if client is not None:
            try:
                client.setex(
                    key,
                    int(ttl_seconds),
                    json.dumps(value, ensure_ascii=False, default=str),
                )
                return
            except RedisError:
                self._redis_checked = False
                self._redis_disabled_until = time.monotonic() + 15.0
        with self._lock:
            self._memory[key] = (time.monotonic() + ttl_seconds, deepcopy(value))
            if len(self._memory) > 1000:
                oldest = sorted(self._memory.items(), key=lambda item: item[1][0])[:100]
                for old_key, _value in oldest:
                    self._memory.pop(old_key, None)

    def delete(self, key: str) -> None:
        client = self._client()
        if client is not None:
            try:
                client.delete(key)
            except RedisError:
                self._redis_checked = False
        with self._lock:
            self._memory.pop(key, None)


_CACHE = RuntimeCache()


def get_runtime_cache() -> RuntimeCache:
    return _CACHE
