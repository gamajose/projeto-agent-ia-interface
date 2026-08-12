from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkProblemORM, CheckmkSiteORM
from app.services.checkmk_master import (
    _host_environment,
    _host_kind,
    _run_master_script,
    _safe_ip,
    master_config,
)
from app.services.noc_skills import select_noc_skill
from app.services.redaction import redact_text


_OPERATION_LOCK = threading.Lock()
_OPERATION_STATE: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "last_started_at": None,
    "last_completed_at": None,
    "last_error": None,
    "sites_seen": 0,
    "sites_ok": 0,
    "sites_failed": 0,
    "hosts_seen": 0,
    "problems_seen": 0,
    "recoveries_seen": 0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _service_state_name(state: int) -> str:
    return {1: "WARN", 2: "CRIT", 3: "UNKNOWN"}.get(int(state), "CRIT")


def _host_state_name(state: int) -> str:
    return {1: "DOWN", 2: "UNREACHABLE"}.get(int(state), "DOWN")


def _problem_key(site_id: str, kind: str, host: str, service: str) -> str:
    return "|".join((str(site_id), str(kind), str(host), str(service)))[:768]


def _livestatus_queries() -> tuple[str, str]:
    """Retorna queries LQL exatamente no formato aceito pelo Livestatus.

    O protocolo e orientado a linhas e exige uma linha em branco no final da
    consulta. Usar ``\\n`` literal envia barra+n pela rede e pode fazer o
    Livestatus responder ``[]`` mesmo com hosts existentes.
    """

    query_hosts = """GET hosts
Columns: name address state
OutputFormat: json

"""
    query_services = """GET services
Columns: host_name host_address description state plugin_output
Filter: state = 1
Filter: state = 2
Filter: state = 3
Or: 3
OutputFormat: json

"""
    return query_hosts, query_services


def _snapshot_script(*, settings) -> str:
    cfg = master_config(settings)
    path = f"/omd/sites/{cfg['site']}/etc/check_mk/multisite.d/sites.mk"
    query_hosts, query_services = _livestatus_queries()
    return f'''from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import ast, json, socket

path = Path({path!r})
tree = ast.parse(path.read_text(encoding="utf-8"))
sites = {{}}
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update" and node.args and isinstance(node.args[0], ast.Dict):
        try:
            value = ast.literal_eval(node.args[0])
        except Exception:
            continue
        if isinstance(value, dict):
            sites.update(value)


def site_meta(site_id, cfg):
    socket_cfg = cfg.get("socket")
    host = None
    port = None
    if isinstance(socket_cfg, tuple) and len(socket_cfg) > 1 and isinstance(socket_cfg[1], dict):
        address = socket_cfg[1].get("address")
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = address[0], address[1]
    status = cfg.get("status_host")
    status_site = status[0] if isinstance(status, tuple) and len(status) > 1 else None
    status_host = status[1] if isinstance(status, tuple) and len(status) > 1 else None
    return {{
        "site_id": str(site_id),
        "alias": str(cfg.get("alias") or site_id),
        "enabled": not bool(cfg.get("disabled")),
        "replication": cfg.get("replication"),
        "livestatus_host": str(host) if host else None,
        "livestatus_port": int(port) if port else None,
        "status_site": str(status_site) if status_site else None,
        "status_host": str(status_host) if status_host else None,
        "multisite_url": str(cfg.get("multisiteurl") or "") or None,
        "is_trusted": bool(cfg.get("is_trusted")),
        "user_sync": cfg.get("user_sync"),
    }}


def query(host, port, payload):
    with socket.create_connection((host, port), timeout={int(cfg['socket_timeout'])}) as sock:
        sock.settimeout({int(cfg['socket_timeout'])})
        sock.sendall(payload.encode())
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    decoded = data.decode(errors="replace").strip()
    if not decoded:
        raise RuntimeError("Livestatus encerrou a conexao sem payload")
    value = json.loads(decoded)
    if not isinstance(value, list):
        raise RuntimeError("Livestatus nao retornou uma lista JSON")
    return value


def collect(site_id, site_cfg):
    meta = site_meta(site_id, site_cfg)
    if not meta["enabled"]:
        return {{"type": "site_snapshot", "site": meta, "queried": False, "ok": True, "hosts": [], "problems": []}}
    host = meta.get("livestatus_host")
    port = meta.get("livestatus_port")
    if not host or not port:
        return {{"type": "site_snapshot", "site": meta, "queried": True, "ok": False, "hosts": [], "problems": [], "error": "socket Livestatus ausente"}}
    try:
        host_rows = query(host, port, {query_hosts!r})
        if not host_rows:
            raise RuntimeError("Livestatus respondeu, mas GET hosts retornou zero linhas")
        service_rows = query(host, port, {query_services!r})
        hosts = []
        problems = []
        for row in host_rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            name = str(row[0] or "")
            address = str(row[1] or "")
            state = int(row[2] or 0)
            hosts.append({{"host": name, "host_address": address, "state": state}})
            if state != 0:
                problems.append({{
                    "kind": "host",
                    "host": name,
                    "host_address": address,
                    "service": "Host status",
                    "state": state,
                    "output": "Host fora de UP conforme Livestatus",
                }})
        if not hosts:
            raise RuntimeError("GET hosts respondeu, mas nenhuma linha valida foi interpretada")
        for row in service_rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            problems.append({{
                "kind": "service",
                "host": str(row[0] or ""),
                "host_address": str(row[1] or ""),
                "service": str(row[2] or ""),
                "state": int(row[3] or 0),
                "output": str(row[4] or ""),
            }})
        return {{
            "type": "site_snapshot",
            "site": meta,
            "queried": True,
            "ok": True,
            "hosts": hosts,
            "problems": problems,
            "host_rows": len(hosts),
            "problem_rows": len(problems),
        }}
    except Exception as exc:
        return {{
            "type": "site_snapshot",
            "site": meta,
            "queried": True,
            "ok": False,
            "hosts": [],
            "problems": [],
            "error": f"{{type(exc).__name__}}: {{exc}}",
        }}

items = list(sorted(sites.items()))[:{int(cfg['max_sites'])}]
disabled = []
active = []
for site_id, site_cfg in items:
    if site_cfg.get("disabled"):
        disabled.append((site_id, site_cfg))
    else:
        active.append((site_id, site_cfg))

for site_id, site_cfg in disabled:
    print(json.dumps(collect(site_id, site_cfg), ensure_ascii=False, separators=(",", ":")))

with ThreadPoolExecutor(max_workers={int(cfg['concurrency'])}) as pool:
    futures = [pool.submit(collect, site_id, site_cfg) for site_id, site_cfg in active]
    for future in as_completed(futures):
        print(json.dumps(future.result(), ensure_ascii=False, separators=(",", ":")))
'''


def _serialize_problem(row: CheckmkProblemORM) -> dict[str, Any]:
    return {
        "problem_key": row.problem_key,
        "site_id": row.site_id,
        "client_alias": row.client_alias,
        "kind": row.kind,
        "host": row.host_name,
        "host_address": row.internal_address,
        "service": row.service,
        "state": row.state,
        "state_name": row.state_name,
        "output": row.output,
        "active": row.active,
        "occurrence_count": row.occurrence_count,
        "skill_id": row.skill_id,
        "skill_title": row.skill_title,
        "route_strategy": row.route_strategy,
        "automation_status": row.automation_status,
        "incident_id": row.incident_id,
        "job_id": row.job_id,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def collect_checkmk_operational_snapshot(*, settings=None) -> dict[str, Any]:
    """Le todos os sites ativos do master e persiste inventario + anomalias.

    Um site que falha nao tem seus problemas anteriores marcados como recovery;
    isso evita falsos verdes quando a propria conexao Livestatus esta indisponivel.
    """

    from app.core.settings import get_settings

    settings = settings or get_settings()
    cfg = master_config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled", "problems": [], "recoveries": [], "site_errors": []}
    if not _OPERATION_LOCK.acquire(blocking=False):
        return {"status": "busy", "problems": [], "recoveries": [], "site_errors": []}

    ensure_database_schema()
    started = _now()
    _OPERATION_STATE.update(
        {
            "running": True,
            "phase": "collecting",
            "last_started_at": started.isoformat(),
            "last_error": None,
        }
    )
    try:
        rows = [
            row
            for row in _run_master_script(_snapshot_script(settings=settings), settings=settings)
            if row.get("type") == "site_snapshot" and isinstance(row.get("site"), dict)
        ]
        endpoint_counts = Counter(
            str((row.get("site") or {}).get("livestatus_host") or "")
            for row in rows
            if (row.get("site") or {}).get("enabled") and (row.get("site") or {}).get("livestatus_host")
        )
        now = _now()
        current_problem_payloads: list[dict[str, Any]] = []
        recoveries: list[dict[str, Any]] = []
        site_errors: list[dict[str, Any]] = []
        hosts_seen = 0
        sites_ok = 0
        sites_failed = 0

        with SessionLocal() as session:
            for snapshot in rows:
                meta = dict(snapshot.get("site") or {})
                site_id = str(meta.get("site_id") or "").strip()
                if not site_id:
                    continue
                alias = str(meta.get("alias") or site_id)[:255]
                site = session.scalar(select(CheckmkSiteORM).where(CheckmkSiteORM.site_id == site_id))
                if site is None:
                    site = CheckmkSiteORM(site_id=site_id, alias=alias)
                    session.add(site)
                site.alias = alias
                site.enabled = bool(meta.get("enabled"))
                site.replication = str(meta.get("replication") or "")[:40] or None
                site.livestatus_host = _safe_ip(meta.get("livestatus_host"))
                site.livestatus_port = int(meta.get("livestatus_port")) if meta.get("livestatus_port") else None
                site.status_site = str(meta.get("status_site") or "")[:64] or None
                site.status_host = str(meta.get("status_host") or "")[:255] or None
                site.multisite_url = str(meta.get("multisite_url") or "")[:1024] or None
                site.shared_endpoint = bool(site.livestatus_host and endpoint_counts.get(site.livestatus_host, 0) > 1)
                site.metadata_payload = {
                    "is_trusted": bool(meta.get("is_trusted")),
                    "user_sync": meta.get("user_sync"),
                    "queried": bool(snapshot.get("queried")),
                }
                site.last_sync_at = now

                if not site.enabled:
                    site.last_error = None
                    continue

                if not snapshot.get("ok"):
                    sites_failed += 1
                    error = redact_text(str(snapshot.get("error") or "falha Livestatus"))[:1800]
                    site.last_error = error
                    site.last_polled_at = now
                    site_errors.append(
                        {
                            "site_id": site_id,
                            "alias": alias,
                            "livestatus_host": site.livestatus_host,
                            "livestatus_port": site.livestatus_port,
                            "error": error,
                        }
                    )
                    continue

                sites_ok += 1
                site.last_error = None
                site.last_polled_at = now
                host_payloads = [item for item in snapshot.get("hosts") or [] if isinstance(item, dict)]
                problem_payloads = [item for item in snapshot.get("problems") or [] if isinstance(item, dict)]
                hosts_seen += len(host_payloads)
                site.host_count = len(host_payloads)
                site.problem_count = len(problem_payloads)

                existing_hosts = {
                    row.host_name: row
                    for row in session.scalars(select(CheckmkHostORM).where(CheckmkHostORM.site_id == site_id)).all()
                }
                seen_host_names: set[str] = set()
                host_kind_by_name: dict[str, str] = {}
                for item in host_payloads:
                    host_name = str(item.get("host") or "").strip()
                    if not host_name:
                        continue
                    seen_host_names.add(host_name)
                    address = _safe_ip(item.get("host_address"))
                    kind = _host_kind(host_name, address or "")
                    host_kind_by_name[host_name] = kind
                    host = existing_hosts.get(host_name)
                    if host is None:
                        host = CheckmkHostORM(site_id=site_id, client_alias=alias, host_name=host_name[:255])
                        session.add(host)
                    host.client_alias = alias
                    host.internal_address = address
                    host.state = int(item.get("state") or 0)
                    host.environment = _host_environment(host_name, kind)
                    host.host_kind = kind
                    host.last_seen_at = now
                    host.metadata_payload = {
                        "entry_host": site.livestatus_host,
                        "entry_livestatus_port": site.livestatus_port,
                    }
                for host_name, host in existing_hosts.items():
                    if host_name not in seen_host_names:
                        session.delete(host)

                existing_problems = {
                    row.problem_key: row
                    for row in session.scalars(
                        select(CheckmkProblemORM).where(CheckmkProblemORM.site_id == site_id)
                    ).all()
                }
                current_keys: set[str] = set()
                active_keys_payload: list[dict[str, Any]] = []
                for item in problem_payloads:
                    kind = str(item.get("kind") or "service")
                    host_name = str(item.get("host") or "").strip()
                    service_name = str(item.get("service") or "Host status").strip()
                    if not host_name or not service_name:
                        continue
                    state = int(item.get("state") or 0)
                    if state == 0:
                        continue
                    key = _problem_key(site_id, kind, host_name, service_name)
                    current_keys.add(key)
                    address = _safe_ip(item.get("host_address"))
                    state_name = _host_state_name(state) if kind == "host" else _service_state_name(state)
                    output = redact_text(str(item.get("output") or ""))[:12000]
                    host_kind = host_kind_by_name.get(host_name) or _host_kind(host_name, address or "")
                    skill = select_noc_skill(
                        {
                            "site_id": site_id,
                            "host": host_name,
                            "host_address": address,
                            "service": service_name,
                            "state": state,
                            "state_name": state_name,
                            "output": output,
                            "host_kind": host_kind,
                        },
                        host_kind=host_kind,
                    )
                    problem = existing_problems.get(key)
                    if problem is None:
                        problem = CheckmkProblemORM(
                            problem_key=key,
                            site_id=site_id,
                            client_alias=alias,
                            kind=kind,
                            host_name=host_name[:255],
                            service=service_name[:512],
                            first_seen_at=now,
                        )
                        session.add(problem)
                    else:
                        problem.occurrence_count = int(problem.occurrence_count or 0) + 1
                    problem.client_alias = alias
                    problem.internal_address = address
                    problem.state = state
                    problem.state_name = state_name
                    problem.output = output
                    problem.active = True
                    problem.last_seen_at = now
                    problem.resolved_at = None
                    problem.skill_id = str(skill.get("id") or "")[:128] or None
                    problem.skill_title = str(skill.get("title") or "")[:255] or None
                    problem.route_strategy = str(skill.get("target_strategy") or "")[:64] or None
                    if problem.automation_status == "resolved":
                        problem.automation_status = "detected"
                        problem.incident_id = None
                        problem.job_id = None
                    problem.metadata_payload = {
                        "entry_host": site.livestatus_host,
                        "entry_livestatus_port": site.livestatus_port,
                        "status_host": site.status_host,
                        "host_kind": host_kind,
                    }
                    payload = {
                        "problem_key": key,
                        "kind": kind,
                        "site_id": site_id,
                        "alias": alias,
                        "entry_host": site.livestatus_host,
                        "entry_port": site.livestatus_port,
                        "host": host_name,
                        "host_address": address,
                        "service": service_name,
                        "state": state,
                        "state_name": state_name,
                        "output": output,
                    }
                    current_problem_payloads.append(payload)
                    active_keys_payload.append(
                        {
                            "key": key,
                            "kind": kind,
                            "host": host_name,
                            "host_address": address,
                            "service": service_name,
                        }
                    )

                for key, problem in existing_problems.items():
                    if problem.active and key not in current_keys:
                        problem.active = False
                        problem.resolved_at = now
                        problem.automation_status = "resolved"
                        recoveries.append(
                            {
                                "problem_key": key,
                                "kind": problem.kind,
                                "site_id": site_id,
                                "alias": alias,
                                "host": problem.host_name,
                                "host_address": problem.internal_address,
                                "service": problem.service,
                            }
                        )
                site.active_problem_keys = active_keys_payload

            session.commit()

        completed = _now()
        _OPERATION_STATE.update(
            {
                "last_completed_at": completed.isoformat(),
                "sites_seen": len(rows),
                "sites_ok": sites_ok,
                "sites_failed": sites_failed,
                "hosts_seen": hosts_seen,
                "problems_seen": len(current_problem_payloads),
                "recoveries_seen": len(recoveries),
                "last_error": None if not site_errors else f"{len(site_errors)} site(s) sem resposta Livestatus",
            }
        )
        return {
            "status": "completed",
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "sites_seen": len(rows),
            "sites_ok": sites_ok,
            "sites_failed": sites_failed,
            "hosts_seen": hosts_seen,
            "problems_seen": len(current_problem_payloads),
            "problems": current_problem_payloads,
            "recoveries": recoveries,
            "site_errors": site_errors,
        }
    except Exception as exc:
        message = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
        _OPERATION_STATE["last_error"] = message
        raise
    finally:
        _OPERATION_STATE.update({"running": False, "phase": "idle"})
        _OPERATION_LOCK.release()


def update_problem_automation(
    problem_key: str,
    *,
    automation_status: str,
    incident_id: str | None = None,
    job_id: str | None = None,
    route: dict[str, Any] | None = None,
) -> None:
    if not problem_key:
        return
    ensure_database_schema()
    with SessionLocal() as session:
        row = session.scalar(select(CheckmkProblemORM).where(CheckmkProblemORM.problem_key == problem_key))
        if row is None:
            return
        row.automation_status = str(automation_status or "detected")[:40]
        if incident_id is not None:
            row.incident_id = str(incident_id)[:64] or None
        if job_id is not None:
            row.job_id = str(job_id)[:64] or None
        if route:
            metadata = dict(row.metadata_payload or {})
            metadata["route"] = {
                "valid": route.get("valid"),
                "reason": route.get("reason"),
                "strategy": route.get("strategy"),
                "entry_address": route.get("entry_address"),
                "internal_address": route.get("internal_address"),
                "shared_endpoint": route.get("shared_endpoint"),
            }
            row.metadata_payload = metadata
        session.commit()


def checkmk_operational_overview(*, problem_limit: int = 500, site_limit: int = 500) -> dict[str, Any]:
    ensure_database_schema()
    with SessionLocal() as session:
        sites = session.scalars(
            select(CheckmkSiteORM)
            .order_by(CheckmkSiteORM.problem_count.desc(), CheckmkSiteORM.alias.asc())
            .limit(max(1, min(int(site_limit), 1000)))
        ).all()
        problems = session.scalars(
            select(CheckmkProblemORM)
            .where(CheckmkProblemORM.active.is_(True))
            .order_by(CheckmkProblemORM.state.desc(), CheckmkProblemORM.last_seen_at.desc())
            .limit(max(1, min(int(problem_limit), 2000)))
        ).all()
        total_sites = int(session.scalar(select(func.count()).select_from(CheckmkSiteORM)) or 0)
        active_sites = int(
            session.scalar(select(func.count()).select_from(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True))) or 0
        )
        total_hosts = int(session.scalar(select(func.count()).select_from(CheckmkHostORM)) or 0)
        active_problems = int(
            session.scalar(select(func.count()).select_from(CheckmkProblemORM).where(CheckmkProblemORM.active.is_(True))) or 0
        )
        failed_sites = [site for site in sites if site.enabled and site.last_error]

        return {
            "state": dict(_OPERATION_STATE),
            "summary": {
                "sites_total": total_sites,
                "sites_active": active_sites,
                "hosts_total": total_hosts,
                "problems_active": active_problems,
                "sites_failed": len(failed_sites),
            },
            "sites": [
                {
                    "site_id": site.site_id,
                    "alias": site.alias,
                    "enabled": site.enabled,
                    "livestatus_host": site.livestatus_host,
                    "livestatus_port": site.livestatus_port,
                    "status_host": site.status_host,
                    "shared_endpoint": site.shared_endpoint,
                    "host_count": site.host_count,
                    "problem_count": site.problem_count,
                    "last_error": site.last_error,
                    "last_sync_at": site.last_sync_at.isoformat() if site.last_sync_at else None,
                    "last_polled_at": site.last_polled_at.isoformat() if site.last_polled_at else None,
                }
                for site in sites
            ],
            "failed_sites": [
                {
                    "site_id": site.site_id,
                    "alias": site.alias,
                    "livestatus_host": site.livestatus_host,
                    "livestatus_port": site.livestatus_port,
                    "error": site.last_error,
                    "last_polled_at": site.last_polled_at.isoformat() if site.last_polled_at else None,
                }
                for site in failed_sites
            ],
            "problems": [_serialize_problem(problem) for problem in problems],
        }


def checkmk_site_detail(site_id: str) -> dict[str, Any] | None:
    ensure_database_schema()
    with SessionLocal() as session:
        site = session.scalar(select(CheckmkSiteORM).where(CheckmkSiteORM.site_id == str(site_id)))
        if site is None:
            return None
        hosts = session.scalars(
            select(CheckmkHostORM)
            .where(CheckmkHostORM.site_id == site.site_id)
            .order_by(CheckmkHostORM.host_name.asc())
        ).all()
        problems = session.scalars(
            select(CheckmkProblemORM)
            .where(CheckmkProblemORM.site_id == site.site_id, CheckmkProblemORM.active.is_(True))
            .order_by(CheckmkProblemORM.state.desc(), CheckmkProblemORM.host_name.asc())
        ).all()
        problems_by_host: Counter[str] = Counter(problem.host_name for problem in problems)
        return {
            "site": {
                "site_id": site.site_id,
                "alias": site.alias,
                "enabled": site.enabled,
                "livestatus_host": site.livestatus_host,
                "livestatus_port": site.livestatus_port,
                "status_host": site.status_host,
                "shared_endpoint": site.shared_endpoint,
                "host_count": site.host_count,
                "problem_count": site.problem_count,
                "last_error": site.last_error,
                "last_polled_at": site.last_polled_at.isoformat() if site.last_polled_at else None,
            },
            "hosts": [
                {
                    "host_name": host.host_name,
                    "internal_address": host.internal_address,
                    "state": host.state,
                    "environment": host.environment,
                    "host_kind": host.host_kind,
                    "problem_count": int(problems_by_host.get(host.host_name, 0)),
                }
                for host in hosts
            ],
            "problems": [_serialize_problem(problem) for problem in problems],
        }
