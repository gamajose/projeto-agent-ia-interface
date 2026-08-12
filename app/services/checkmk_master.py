from __future__ import annotations

import base64
import ipaddress
import json
import shlex
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkSiteORM
from app.services.redaction import redact_text
from app.services.runtime_env import runtime_bool, runtime_int, runtime_value
from app.services.secrets import get_secret
from app.services.ssh import SSHExecutor


_MASTER_LOCK = threading.Lock()
_MASTER_STATE: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "last_sync_at": None,
    "last_poll_at": None,
    "last_error": None,
    "sites_total": 0,
    "sites_active": 0,
    "sites_disabled": 0,
    "hosts_total": 0,
    "problems": 0,
    "recoveries": 0,
    "recent_problems": [],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def master_config(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "enabled": runtime_bool("CHECKMK_MASTER_ENABLED", True, settings=settings),
        "target": str(
            runtime_value(
                "CHECKMK_MASTER_TARGET",
                runtime_value("SSH_CMK05", runtime_value("SSH_CMK05_IP", "10.17.181.44", settings=settings), settings=settings),
                settings=settings,
            )
            or "10.17.181.44"
        ).strip(),
        "ssh_port": runtime_int("CHECKMK_MASTER_SSH_PORT", 22, minimum=1, maximum=65535, settings=settings),
        "ssh_user": str(runtime_value("CHECKMK_MASTER_SSH_USER", settings.ssh_default_user, settings=settings) or settings.ssh_default_user).strip(),
        "container": str(runtime_value("CHECKMK_MASTER_CONTAINER", "checkmk-master-25", settings=settings) or "checkmk-master-25").strip(),
        "site": str(runtime_value("CHECKMK_MASTER_SITE", "master", settings=settings) or "master").strip(),
        "timeout": runtime_int("CHECKMK_MASTER_COMMAND_TIMEOUT_SECONDS", 120, minimum=20, maximum=900, settings=settings),
        "socket_timeout": runtime_int("CHECKMK_MASTER_SOCKET_TIMEOUT_SECONDS", 10, minimum=2, maximum=60, settings=settings),
        "concurrency": runtime_int("CHECKMK_MASTER_CONCURRENCY", 16, minimum=1, maximum=64, settings=settings),
        "max_sites": runtime_int("CHECKMK_MASTER_MAX_SITES", 1000, minimum=1, maximum=5000, settings=settings),
        "max_records": runtime_int("CHECKMK_MASTER_MAX_RECORDS", 50000, minimum=100, maximum=500000, settings=settings),
    }


def _master_executor(settings: Settings) -> SSHExecutor:
    cfg = master_config(settings)
    password = get_secret("CHECKMK_MASTER_SSH_PASSWORD", None, settings=settings)
    if not password:
        password = get_secret("SSH_DEFAULT_PASSWORD", settings.ssh_default_password, settings=settings)
    return SSHExecutor(
        host=str(cfg["target"]),
        port=int(cfg["ssh_port"]),
        username=str(cfg["ssh_user"]),
        password=password,
        connect_timeout=settings.ssh_connect_timeout,
        private_key_path=settings.ssh_private_key_path,
        private_key_passphrase=get_secret(
            "SSH_PRIVATE_KEY_PASSPHRASE",
            settings.ssh_private_key_passphrase,
            settings=settings,
        ),
        allow_agent=settings.ssh_allow_agent,
        look_for_keys=settings.ssh_look_for_keys,
        strict_host_key_checking=settings.ssh_strict_host_key_checking,
        known_hosts_path=settings.ssh_known_hosts_path,
    )


def _docker_python(script: str, *, settings: Settings) -> str:
    cfg = master_config(settings)
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    python = f"import base64;exec(base64.b64decode({payload!r}))"
    return f"docker exec {shlex.quote(str(cfg['container']))} python3 -c {shlex.quote(python)}"


def _run_master_script(script: str, *, settings: Settings) -> list[dict[str, Any]]:
    cfg = master_config(settings)
    executor = _master_executor(settings)
    try:
        executor.connect()
        result = executor.run_sudo(
            _docker_python(script, settings=settings),
            EnvironmentType.UNKNOWN,
            approved=False,
            timeout=int(cfg["timeout"]),
        )
        if result.exit_code != 0:
            raise RuntimeError(redact_text(result.stderr or result.stdout or f"codigo {result.exit_code}"))
        rows: list[dict[str, Any]] = []
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows
    finally:
        executor.close()


def _sites_script(*, settings: Settings) -> str:
    cfg = master_config(settings)
    path = f"/omd/sites/{cfg['site']}/etc/check_mk/multisite.d/sites.mk"
    return f'''from pathlib import Path
import ast, json
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
for site_id, cfg in sorted(sites.items()):
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
    print(json.dumps({{
        "type": "site",
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
    }}, ensure_ascii=False))
'''


def _query_script(mode: str, *, settings: Settings) -> str:
    cfg = master_config(settings)
    path = f"/omd/sites/{cfg['site']}/etc/check_mk/multisite.d/sites.mk"
    query_hosts = "GET hosts\\nColumns: name address state\\nOutputFormat: json\\n\\n"
    query_services = "GET services\\nColumns: host_name host_address description state plugin_output last_check\\nFilter: state >= 1\\nOutputFormat: json\\n\\n"
    query_bad_hosts = "GET hosts\\nColumns: name address state plugin_output last_check\\nFilter: state >= 1\\nOutputFormat: json\\n\\n"
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

def endpoint(cfg):
    socket_cfg = cfg.get("socket")
    if not (isinstance(socket_cfg, tuple) and len(socket_cfg) > 1 and isinstance(socket_cfg[1], dict)):
        return None
    address = socket_cfg[1].get("address")
    if not (isinstance(address, tuple) and len(address) >= 2):
        return None
    return str(address[0]), int(address[1])

def query(host, port, payload):
    with socket.create_connection((host, port), timeout={int(cfg['socket_timeout'])}) as sock:
        sock.sendall(payload.encode())
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data.decode(errors="replace") or "[]")

def collect(site_id, cfg):
    ep = endpoint(cfg)
    if not ep:
        return [{{"type":"error","site_id":str(site_id),"error":"socket Livestatus ausente"}}]
    host, port = ep
    alias = str(cfg.get("alias") or site_id)
    result = []
    try:
        if {mode!r} == "hosts":
            rows = query(host, port, {query_hosts!r})
            for row in rows:
                if isinstance(row, list) and len(row) >= 3:
                    result.append({{"type":"host","site_id":str(site_id),"alias":alias,"entry_host":host,"entry_port":port,"host":str(row[0]),"host_address":str(row[1] or ""),"state":int(row[2] or 0)}})
        else:
            rows = query(host, port, {query_services!r})
            for row in rows:
                if isinstance(row, list) and len(row) >= 6:
                    result.append({{"type":"problem","kind":"service","site_id":str(site_id),"alias":alias,"entry_host":host,"entry_port":port,"host":str(row[0]),"host_address":str(row[1] or ""),"service":str(row[2]),"state":int(row[3] or 0),"output":str(row[4] or ""),"last_check":row[5]}})
            host_rows = query(host, port, {query_bad_hosts!r})
            for row in host_rows:
                if isinstance(row, list) and len(row) >= 5:
                    result.append({{"type":"problem","kind":"host","site_id":str(site_id),"alias":alias,"entry_host":host,"entry_port":port,"host":str(row[0]),"host_address":str(row[1] or ""),"service":"Host status","state":int(row[2] or 0),"output":str(row[3] or ""),"last_check":row[4]}})
    except Exception as exc:
        result.append({{"type":"error","site_id":str(site_id),"alias":alias,"entry_host":host,"entry_port":port,"error":f"{{type(exc).__name__}}: {{exc}}"}})
    return result

active = [(site_id, cfg) for site_id, cfg in sorted(sites.items()) if not cfg.get("disabled")][:{int(cfg['max_sites'])}]
with ThreadPoolExecutor(max_workers={int(cfg['concurrency'])}) as pool:
    futures = [pool.submit(collect, site_id, cfg) for site_id, cfg in active]
    emitted = 0
    for future in as_completed(futures):
        for item in future.result():
            print(json.dumps(item, ensure_ascii=False))
            emitted += 1
            if emitted >= {int(cfg['max_records'])}:
                raise SystemExit(0)
'''


def _host_kind(name: str, address: str) -> str:
    text = str(name or "").casefold()
    if address in {"", "0.0.0.0", "127.0.0.1", "::1"} or text.startswith("checkmk-"):
        return "monitoring_local"
    if any(marker in text for marker in ("idrac", "ilom", "-ilo", "_ilo", "bmc")):
        return "bmc"
    if any(marker in text for marker in ("firewall", "pfsense", "fortigate", "forti-")):
        return "firewall"
    return "server"


def _host_environment(name: str, kind: str) -> str:
    text = str(name or "").casefold()
    if kind == "monitoring_local" or "monitor" in text:
        return EnvironmentType.MONITORING.value
    if "standby" in text or "stby" in text:
        return EnvironmentType.STANDBY.value
    if any(marker in text for marker in ("prod", "primario", "primary")):
        return EnvironmentType.PRODUCTION.value
    if any(marker in text for marker in ("trein", "training", "homolog", "hml", "preprod", "pré-prod")):
        return EnvironmentType.TRAINING.value
    return EnvironmentType.UNKNOWN.value


def _safe_ip(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text[:64]


def sync_checkmk_master_inventory(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    cfg = master_config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    if not _MASTER_LOCK.acquire(blocking=False):
        return {"status": "busy"}
    ensure_database_schema()
    started = _now()
    _MASTER_STATE.update({"running": True, "phase": "inventory_sync", "last_error": None})
    try:
        site_rows = [row for row in _run_master_script(_sites_script(settings=settings), settings=settings) if row.get("type") == "site"]
        endpoint_counts = Counter(str(row.get("livestatus_host") or "") for row in site_rows if row.get("enabled") and row.get("livestatus_host"))
        site_lookup = {str(row.get("site_id")): row for row in site_rows if row.get("site_id")}
        host_rows_all = _run_master_script(_query_script("hosts", settings=settings), settings=settings)
        host_rows = [row for row in host_rows_all if row.get("type") == "host"]
        errors = {str(row.get("site_id")): redact_text(str(row.get("error") or ""))[:1800] for row in host_rows_all if row.get("type") == "error"}
        host_counts = Counter(str(row.get("site_id")) for row in host_rows)
        now = _now()

        with SessionLocal() as session:
            for item in site_rows:
                site_id = str(item.get("site_id") or "").strip()
                if not site_id:
                    continue
                row = session.scalar(select(CheckmkSiteORM).where(CheckmkSiteORM.site_id == site_id))
                if not row:
                    row = CheckmkSiteORM(site_id=site_id, alias=str(item.get("alias") or site_id))
                    session.add(row)
                row.alias = str(item.get("alias") or site_id)[:255]
                row.enabled = bool(item.get("enabled"))
                row.replication = str(item.get("replication") or "")[:40] or None
                row.livestatus_host = _safe_ip(item.get("livestatus_host"))
                row.livestatus_port = int(item.get("livestatus_port")) if item.get("livestatus_port") else None
                row.status_site = str(item.get("status_site") or "")[:64] or None
                row.status_host = str(item.get("status_host") or "")[:255] or None
                row.multisite_url = str(item.get("multisite_url") or "")[:1024] or None
                row.shared_endpoint = bool(row.livestatus_host and endpoint_counts.get(row.livestatus_host, 0) > 1)
                row.host_count = int(host_counts.get(site_id, 0))
                row.metadata_payload = {
                    "is_trusted": bool(item.get("is_trusted")),
                    "user_sync": item.get("user_sync"),
                }
                row.last_sync_at = now
                row.last_error = errors.get(site_id)

            for item in host_rows:
                site_id = str(item.get("site_id") or "").strip()
                host_name = str(item.get("host") or "").strip()
                if not site_id or not host_name:
                    continue
                site_cfg = site_lookup.get(site_id) or {}
                address = _safe_ip(str(item.get("host_address") or ""))
                kind = _host_kind(host_name, address or "")
                environment = _host_environment(host_name, kind)
                row = session.scalar(
                    select(CheckmkHostORM).where(
                        CheckmkHostORM.site_id == site_id,
                        CheckmkHostORM.host_name == host_name,
                    )
                )
                if not row:
                    row = CheckmkHostORM(
                        site_id=site_id,
                        client_alias=str(item.get("alias") or site_cfg.get("alias") or site_id)[:255],
                        host_name=host_name[:255],
                    )
                    session.add(row)
                row.client_alias = str(item.get("alias") or site_cfg.get("alias") or site_id)[:255]
                row.internal_address = address
                row.state = int(item.get("state") or 0)
                row.environment = environment
                row.host_kind = kind
                row.last_seen_at = now
                row.metadata_payload = {
                    "entry_host": _safe_ip(item.get("entry_host")),
                    "entry_livestatus_port": int(item.get("entry_port") or 0) or None,
                }
            session.commit()

        total = len(site_rows)
        active = sum(1 for row in site_rows if row.get("enabled"))
        _MASTER_STATE.update(
            {
                "last_sync_at": now.isoformat(),
                "sites_total": total,
                "sites_active": active,
                "sites_disabled": total - active,
                "hosts_total": len(host_rows),
                "last_error": None if not errors else f"{len(errors)} site(s) sem inventario no ciclo",
            }
        )
        return {
            "status": "completed",
            "started_at": started.isoformat(),
            "completed_at": now.isoformat(),
            "sites_total": total,
            "sites_active": active,
            "sites_disabled": total - active,
            "hosts_total": len(host_rows),
            "site_errors": len(errors),
        }
    except Exception as exc:
        message = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
        _MASTER_STATE["last_error"] = message
        raise
    finally:
        _MASTER_STATE.update({"running": False, "phase": "idle"})
        _MASTER_LOCK.release()


def _problem_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            str(item.get("kind") or "service"),
            str(item.get("host") or ""),
            str(item.get("service") or ""),
        ]
    )


def poll_checkmk_master_problems(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    cfg = master_config(settings)
    if not cfg["enabled"]:
        return {"status": "disabled", "problems": [], "recoveries": []}
    if not _MASTER_LOCK.acquire(blocking=False):
        return {"status": "busy", "problems": [], "recoveries": []}
    ensure_database_schema()
    _MASTER_STATE.update({"running": True, "phase": "problem_poll", "last_error": None})
    try:
        raw_rows = _run_master_script(_query_script("problems", settings=settings), settings=settings)
        problems = [row for row in raw_rows if row.get("type") == "problem"]
        errors = {str(row.get("site_id")): redact_text(str(row.get("error") or ""))[:1800] for row in raw_rows if row.get("type") == "error"}
        by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in problems:
            item["key"] = _problem_key(item)
            item["state_name"] = {1: "WARN", 2: "CRIT", 3: "UNKNOWN"}.get(int(item.get("state") or 2), "CRIT")
            by_site[str(item.get("site_id") or "")].append(item)

        recoveries: list[dict[str, Any]] = []
        now = _now()
        with SessionLocal() as session:
            sites = session.scalars(select(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True))).all()
            for site in sites:
                current = by_site.get(site.site_id, [])
                previous = [dict(item) for item in site.active_problem_keys or [] if isinstance(item, dict)]
                current_keys = {str(item.get("key") or _problem_key(item)) for item in current}
                for old in previous:
                    old_key = str(old.get("key") or _problem_key(old))
                    if old_key and old_key not in current_keys:
                        recoveries.append({**old, "site_id": site.site_id, "alias": site.alias})
                site.active_problem_keys = [
                    {
                        "key": item.get("key"),
                        "kind": item.get("kind"),
                        "host": item.get("host"),
                        "host_address": item.get("host_address"),
                        "service": item.get("service"),
                    }
                    for item in current
                ]
                site.problem_count = len(current)
                site.last_polled_at = now
                site.last_error = errors.get(site.site_id)
            session.commit()

        _MASTER_STATE.update(
            {
                "last_poll_at": now.isoformat(),
                "problems": len(problems),
                "recoveries": len(recoveries),
                "recent_problems": [
                    {
                        "site_id": item.get("site_id"),
                        "alias": item.get("alias"),
                        "host": item.get("host"),
                        "host_address": item.get("host_address"),
                        "service": item.get("service"),
                        "state": item.get("state_name"),
                        "output": redact_text(str(item.get("output") or ""))[:500],
                    }
                    for item in problems[:30]
                ],
                "last_error": None if not errors else f"{len(errors)} site(s) sem resposta Livestatus",
            }
        )
        return {
            "status": "completed",
            "completed_at": now.isoformat(),
            "problems": problems,
            "recoveries": recoveries,
            "site_errors": errors,
        }
    except Exception as exc:
        message = redact_text(f"{type(exc).__name__}: {exc}")[:2000]
        _MASTER_STATE["last_error"] = message
        raise
    finally:
        _MASTER_STATE.update({"running": False, "phase": "idle"})
        _MASTER_LOCK.release()


def checkmk_master_status(*, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    cfg = master_config(settings)
    ensure_database_schema()
    with SessionLocal() as session:
        total = int(session.scalar(select(func.count()).select_from(CheckmkSiteORM)) or 0)
        active = int(
            session.scalar(select(func.count()).select_from(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True))) or 0
        )
        hosts = int(session.scalar(select(func.count()).select_from(CheckmkHostORM)) or 0)
        problems = int(session.scalar(select(func.coalesce(func.sum(CheckmkSiteORM.problem_count), 0))) or 0)
        shared = int(
            session.scalar(
                select(func.count()).select_from(CheckmkSiteORM).where(
                    CheckmkSiteORM.enabled.is_(True), CheckmkSiteORM.shared_endpoint.is_(True)
                )
            )
            or 0
        )
    state = dict(_MASTER_STATE)
    state.update(
        {
            "enabled": bool(cfg["enabled"]),
            "source": "CMK05/master",
            "target": cfg["target"],
            "container": cfg["container"],
            "site": cfg["site"],
            "sites_total": total or int(state.get("sites_total") or 0),
            "sites_active": active or int(state.get("sites_active") or 0),
            "sites_disabled": max(0, (total or int(state.get("sites_total") or 0)) - (active or int(state.get("sites_active") or 0))),
            "hosts_total": hosts or int(state.get("hosts_total") or 0),
            "problems": problems if total else int(state.get("problems") or 0),
            "shared_endpoint_sites": shared,
        }
    )
    return state


def site_and_host(site_id: str, host_name: str) -> tuple[CheckmkSiteORM | None, CheckmkHostORM | None]:
    ensure_database_schema()
    with SessionLocal() as session:
        site = session.scalar(select(CheckmkSiteORM).where(CheckmkSiteORM.site_id == str(site_id)))
        host = session.scalar(
            select(CheckmkHostORM).where(
                CheckmkHostORM.site_id == str(site_id),
                CheckmkHostORM.host_name == str(host_name),
            )
        )
        if site:
            session.expunge(site)
        if host:
            session.expunge(host)
        return site, host
