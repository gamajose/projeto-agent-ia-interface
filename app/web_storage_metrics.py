from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Request
from redis import Redis
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.base import SessionLocal
from app.web import _require_access


router = APIRouter(tags=["interface-storage-metrics"])


def _postgres_metrics() -> dict:
    started = perf_counter()
    try:
        with SessionLocal() as session:
            database = session.execute(text(
                "SELECT pg_database_size(current_database()) AS database_bytes, "
                "xact_commit, xact_rollback, blks_read, blks_hit "
                "FROM pg_stat_database WHERE datname = current_database()"
            )).mappings().one()
            totals = session.execute(text(
                "SELECT COUNT(*) AS table_count, "
                "COALESCE(SUM(pg_relation_size(relid)), 0) AS table_bytes, "
                "COALESCE(SUM(pg_indexes_size(relid)), 0) AS index_bytes, "
                "COALESCE(SUM(n_live_tup), 0) AS estimated_rows "
                "FROM pg_stat_user_tables"
            )).mappings().one()
            connections = int(session.execute(text(
                "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()"
            )).scalar_one())
            tables = session.execute(text(
                "SELECT relname AS name, pg_total_relation_size(relid) AS total_bytes, "
                "pg_relation_size(relid) AS table_bytes, pg_indexes_size(relid) AS index_bytes, "
                "n_live_tup AS estimated_rows FROM pg_stat_user_tables "
                "ORDER BY pg_total_relation_size(relid) DESC, relname LIMIT 8"
            )).mappings().all()
        hit = int(database.get("blks_hit") or 0)
        read = int(database.get("blks_read") or 0)
        return {
            "state": "available",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "database_bytes": int(database.get("database_bytes") or 0),
            "table_bytes": int(totals.get("table_bytes") or 0),
            "index_bytes": int(totals.get("index_bytes") or 0),
            "table_count": int(totals.get("table_count") or 0),
            "estimated_rows": int(totals.get("estimated_rows") or 0),
            "connections": connections,
            "cache_hit_percent": round((hit / (hit + read)) * 100, 2) if hit + read else 100.0,
            "transactions": int(database.get("xact_commit") or 0) + int(database.get("xact_rollback") or 0),
            "tables": [dict(row) for row in tables],
        }
    except Exception as exc:
        return {
            "state": "unavailable",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "detail": f"{type(exc).__name__}: não foi possível coletar métricas do PostgreSQL.",
            "tables": [],
        }


def _redis_metrics() -> dict:
    settings = get_settings()
    started = perf_counter()
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
        client.ping()
        memory = client.info("memory")
        clients = client.info("clients")
        return {
            "state": "available",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "used_memory_bytes": int(memory.get("used_memory") or 0),
            "used_memory_rss_bytes": int(memory.get("used_memory_rss") or 0),
            "maxmemory_bytes": int(memory.get("maxmemory") or 0),
            "fragmentation_ratio": float(memory.get("mem_fragmentation_ratio") or 0),
            "keys": int(client.dbsize()),
            "connected_clients": int(clients.get("connected_clients") or 0),
            "queue_depth": int(client.llen(settings.agent_queue_name)),
            "queue": settings.agent_queue_name,
        }
    except Exception as exc:
        return {
            "state": "unavailable",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "detail": f"{type(exc).__name__}: não foi possível coletar métricas do Redis.",
        }


@router.get("/ui/api/observability/storage")
def storage_metrics(request: Request) -> dict:
    _require_access(request)
    return {"postgres": _postgres_metrics(), "redis": _redis_metrics()}
