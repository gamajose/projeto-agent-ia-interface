from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from redis import Redis

from app.core.settings import Settings, get_settings


_CONTROL_SUFFIX = "autonomy:control"
_PENDING_RUNS_SUFFIX = "autonomy:runs:pending"
_RUN_SUFFIX = "autonomy:run"
_RUN_TTL_SECONDS = 7200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _prefix(settings: Settings) -> str:
    return str(getattr(settings, "noc_incident_prefix", "agent-ia:noc") or "agent-ia:noc").rstrip(":")


def _control_key(settings: Settings) -> str:
    return f"{_prefix(settings)}:{_CONTROL_SUFFIX}"


def _pending_runs_key(settings: Settings) -> str:
    return f"{_prefix(settings)}:{_PENDING_RUNS_SUFFIX}"


def _run_key(settings: Settings, run_id: str) -> str:
    return f"{_prefix(settings)}:{_RUN_SUFFIX}:{run_id}"


def _unique(values: list[str] | tuple[str, ...] | None, *, limit: int = 1000) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _default_control() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "automatic",
        "sites": [],
        "hosts": [],
        "problem_keys": [],
        "revision": None,
        "updated_at": None,
        "updated_by": None,
        "default_off": True,
    }


def _decode(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def get_noc_autonomy_control(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    payload = _decode(_redis(settings).get(_control_key(settings)))
    if not payload:
        return _default_control()
    return {**_default_control(), **payload}


def update_noc_autonomy_control(
    *,
    enabled: bool,
    mode: str,
    sites: list[str] | None = None,
    hosts: list[str] | None = None,
    problem_keys: list[str] | None = None,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    normalized_mode = str(mode or "automatic").strip().lower()
    if normalized_mode not in {"automatic", "selected"}:
        raise ValueError("modo de autonomia inválido; use automatic ou selected")

    normalized_sites = _unique(sites)
    normalized_hosts = _unique(hosts)
    normalized_problems = _unique(problem_keys, limit=5000)
    if enabled and normalized_mode == "selected" and not any((normalized_sites, normalized_hosts, normalized_problems)):
        raise ValueError("modo selecionado exige ao menos um cliente, host ou problema")

    now = _now()
    payload = {
        "enabled": bool(enabled),
        "mode": normalized_mode,
        "sites": normalized_sites,
        "hosts": normalized_hosts,
        "problem_keys": normalized_problems,
        "revision": str(uuid.uuid4()),
        "updated_at": now,
        "updated_by": str(operator or "operator"),
        "default_off": True,
    }
    _redis(settings).set(_control_key(settings), json.dumps(payload, ensure_ascii=False))
    return payload


def scope_matches_problem(problem: dict[str, Any], scope: dict[str, Any]) -> bool:
    if not bool(scope.get("enabled")):
        return False
    mode = str(scope.get("mode") or "automatic").lower()
    if mode == "automatic":
        return True
    if mode != "selected":
        return False

    sites = set(_unique(list(scope.get("sites") or [])))
    hosts = set(_unique(list(scope.get("hosts") or [])))
    problem_keys = set(_unique(list(scope.get("problem_keys") or []), limit=5000))
    if not any((sites, hosts, problem_keys)):
        return False

    site_id = str(problem.get("site_id") or problem.get("site") or "").strip()
    host = str(problem.get("host") or "").strip()
    problem_key = str(problem.get("problem_key") or "").strip()
    if sites and site_id not in sites:
        return False
    if hosts and host not in hosts:
        return False
    if problem_keys and problem_key not in problem_keys:
        return False
    return True


def problem_authorization(
    problem: dict[str, Any],
    *,
    scope_override: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    scope = dict(scope_override or get_noc_autonomy_control(settings=settings))
    allowed = scope_matches_problem(problem, scope)
    if allowed:
        reason = "escopo autônomo autorizado pelo operador"
    elif not scope.get("enabled"):
        reason = "agentes em modo observação; atuação autônoma desligada"
    elif str(scope.get("mode") or "") == "selected":
        reason = "problema fora do escopo selecionado pelo operador"
    else:
        reason = "atuação não autorizada"
    return {"allowed": allowed, "reason": reason, "scope": scope}


def request_selected_run(
    *,
    sites: list[str] | None = None,
    hosts: list[str] | None = None,
    problem_keys: list[str] | None = None,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    scope = {
        "enabled": True,
        "mode": "selected",
        "sites": _unique(sites),
        "hosts": _unique(hosts),
        "problem_keys": _unique(problem_keys, limit=5000),
    }
    if not any((scope["sites"], scope["hosts"], scope["problem_keys"])):
        raise ValueError("selecione ao menos um cliente, host ou problema antes de executar")

    run_id = str(uuid.uuid4())
    run = {
        "id": run_id,
        "status": "queued",
        "scope": scope,
        "created_at": _now(),
        "updated_at": _now(),
        "requested_by": str(operator or "operator"),
        "result": None,
    }
    client = _redis(settings)
    client.setex(_run_key(settings, run_id), _RUN_TTL_SECONDS, json.dumps(run, ensure_ascii=False))
    client.rpush(_pending_runs_key(settings), run_id)
    return run


def get_selected_run(run_id: str, *, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    return _decode(_redis(settings).get(_run_key(settings, run_id)))


def next_selected_run(*, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = _redis(settings)
    run_id = client.lpop(_pending_runs_key(settings))
    if not run_id:
        return None
    run = get_selected_run(str(run_id), settings=settings)
    if not run:
        return None
    run["status"] = "running"
    run["updated_at"] = _now()
    client.setex(_run_key(settings, str(run_id)), _RUN_TTL_SECONDS, json.dumps(run, ensure_ascii=False))
    return run


def requeue_selected_run(run: dict[str, Any], *, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    client = _redis(settings)
    run = dict(run)
    run["status"] = "queued"
    run["updated_at"] = _now()
    client.setex(_run_key(settings, str(run["id"])), _RUN_TTL_SECONDS, json.dumps(run, ensure_ascii=False))
    client.lpush(_pending_runs_key(settings), str(run["id"]))
    return run


def complete_selected_run(
    run: dict[str, Any],
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    payload = dict(run)
    payload["status"] = "completed" if result.get("status") == "completed" else str(result.get("status") or "failed")
    payload["updated_at"] = _now()
    payload["completed_at"] = payload["updated_at"]
    payload["result"] = result
    _redis(settings).setex(
        _run_key(settings, str(payload["id"])),
        _RUN_TTL_SECONDS,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    return payload


def authorize_noc_job(metadata: dict[str, Any], *, settings: Settings | None = None) -> tuple[bool, str]:
    settings = settings or get_settings()
    source = str(metadata.get("source") or "")
    if source not in {"checkmk_master", "noc_reinvestigation"}:
        return True, "job não pertence ao fluxo autônomo NOC"

    problem = {
        "site_id": metadata.get("site_id"),
        "host": metadata.get("checkmk_host") or metadata.get("host"),
        "problem_key": metadata.get("checkmk_problem_key") or metadata.get("problem_key"),
    }
    run_id = str(metadata.get("noc_run_id") or "").strip()
    if run_id:
        run = get_selected_run(run_id, settings=settings)
        if not run:
            return False, "autorização pontual do NOC expirou"
        scope = dict(run.get("scope") or {})
        return (
            (True, "execução pontual autorizada pelo operador")
            if scope_matches_problem(problem, scope)
            else (False, "job não pertence ao escopo da execução pontual")
        )

    control = get_noc_autonomy_control(settings=settings)
    revision = str(metadata.get("noc_control_revision") or "")
    if not control.get("enabled"):
        return False, "atuação autônoma foi desligada pelo operador"
    if not revision or revision != str(control.get("revision") or ""):
        return False, "escopo autônomo mudou depois que o job entrou na fila"
    if not scope_matches_problem(problem, control):
        return False, "job não pertence mais ao escopo autônomo atual"
    return True, "job autorizado pelo escopo autônomo atual"
