from __future__ import annotations

from typing import Any

from sqlalchemy import and_, select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import (
    CheckmkProblemORM,
    NocActionHistoryORM,
    NocAutomationPolicyORM,
)
from app.services.redaction import redact_text


_POLICY_DEFAULTS: tuple[dict[str, Any], ...] = (
    {
        "category": "checkmk_runtime",
        "label": "Checkmk / OMD",
        "enabled": True,
        "immutable": False,
        "description": "Runtime do Checkmk, OMD e processos internos de monitoramento.",
    },
    {
        "category": "monitoring_sensor",
        "label": "Sensores de monitoramento",
        "enabled": True,
        "immutable": False,
        "description": "Sensores e serviços responsáveis pela coleta do monitoramento.",
    },
    {
        "category": "host_check",
        "label": "Host Check / Check_MK Agent",
        "enabled": True,
        "immutable": False,
        "description": "Check_MK Agent, Host status, Discovery e checks diretamente ligados à coleta do host.",
    },
    {
        "category": "filesystem",
        "label": "Filesystem / armazenamento",
        "enabled": False,
        "immutable": False,
        "description": "Disco, filesystem, inode, mounts e capacidade. Desabilitado por padrão.",
    },
    {
        "category": "database",
        "label": "Banco de dados / backup",
        "enabled": False,
        "immutable": False,
        "description": "Oracle, MSSQL, PostgreSQL, RMAN, Datapump e rotinas de banco. Desabilitado por padrão.",
    },
    {
        "category": "memory",
        "label": "Memória / swap",
        "enabled": False,
        "immutable": False,
        "description": "Pressão de memória e swap. Investigação é permitida; correção autônoma fica desabilitada.",
    },
    {
        "category": "network",
        "label": "Rede / link / firewall",
        "enabled": False,
        "immutable": False,
        "description": "Interfaces, gateways, links e firewalls. Desabilitado por padrão.",
    },
    {
        "category": "bmc_snmp",
        "label": "SNMP / iDRAC / ILOM / BMC",
        "enabled": False,
        "immutable": False,
        "description": "Hardware, SNMP e controladoras de gerenciamento. Desabilitado por padrão.",
    },
    {
        "category": "other",
        "label": "Outros",
        "enabled": False,
        "immutable": False,
        "description": "Categorias ainda não classificadas explicitamente.",
    },
    {
        "category": "server_reboot",
        "label": "Reiniciar servidor",
        "enabled": False,
        "immutable": True,
        "description": "Bloqueio absoluto. O Agent IA nunca reinicia, desliga ou liga o servidor.",
    },
)


def ensure_noc_policy_defaults() -> None:
    ensure_database_schema()
    with SessionLocal() as session:
        existing = {
            row.category: row
            for row in session.scalars(select(NocAutomationPolicyORM)).all()
        }
        changed = False
        for item in _POLICY_DEFAULTS:
            row = existing.get(str(item["category"]))
            if row is None:
                session.add(NocAutomationPolicyORM(**item))
                changed = True
                continue
            # Reboot é uma trava de segurança e nunca pode ser habilitado por dado antigo.
            if row.category == "server_reboot":
                row.enabled = False
                row.immutable = True
                row.label = str(item["label"])
                row.description = str(item["description"])
                changed = True
        if changed:
            session.commit()


def list_noc_automation_policies() -> dict[str, Any]:
    ensure_noc_policy_defaults()
    order = {str(item["category"]): index for index, item in enumerate(_POLICY_DEFAULTS)}
    with SessionLocal() as session:
        rows = session.scalars(select(NocAutomationPolicyORM)).all()
    items = sorted(rows, key=lambda row: order.get(row.category, 999))
    return {
        "items": [
            {
                "category": row.category,
                "label": row.label,
                "enabled": bool(row.enabled),
                "immutable": bool(row.immutable),
                "description": row.description,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in items
        ]
    }


def update_noc_automation_policy(category: str, enabled: bool) -> dict[str, Any]:
    ensure_noc_policy_defaults()
    normalized = str(category or "").strip()
    with SessionLocal() as session:
        row = session.scalar(
            select(NocAutomationPolicyORM).where(NocAutomationPolicyORM.category == normalized)
        )
        if row is None:
            raise ValueError("categoria de automação desconhecida")
        if row.immutable:
            row.enabled = False
            session.commit()
            raise ValueError("esta política é uma trava de segurança e não pode ser habilitada")
        row.enabled = bool(enabled)
        session.commit()
        session.refresh(row)
        return {
            "category": row.category,
            "label": row.label,
            "enabled": bool(row.enabled),
            "immutable": bool(row.immutable),
            "description": row.description,
        }


def classify_problem_category(event: dict[str, Any]) -> str:
    service = str(event.get("service") or "").casefold()
    host = str(event.get("host") or event.get("host_name") or "").casefold()
    output = str(event.get("output") or event.get("last_output") or "").casefold()
    skill = str(event.get("skill_id") or "").casefold()
    text = " ".join((service, host, output, skill))

    if any(token in text for token in ("filesystem", "inode", "mount", "disk space", "df ", "storage", "disco ")):
        return "filesystem"
    if any(token in text for token in (
        "oracle", "rman", "datapump", "archive", "archivelog", "mssql", "sqlserver", "postgres",
        "database", "deadlock", "backup_datapump", "ora ", "ora_", "db2", "mysql",
    )):
        return "database"
    if any(token in text for token in ("memory", "swap", "memória", "memoria")):
        return "memory"
    if any(token in text for token in ("idrac", "ilom", "bmc", "ipmi", "snmp", "hardware")):
        return "bmc_snmp"
    if any(token in text for token in ("gateway", "packet loss", "interface", "firewall", "link ", "latency", "latência")):
        return "network"
    if any(token in text for token in (
        "automation helper", "automation-helper", "omd ", "omd_", "rrdcached", "mkeventd", "agent-receiver",
        "ui-job-scheduler", "npcd", "checkmk-", "checkmk_",
    )) or host.startswith("checkmk-"):
        return "checkmk_runtime"
    if any(token in text for token in (
        "check_mk agent", "check mk agent", "check_mk discovery", "check mk discovery", "check_mk hw/sw inventory",
        "check mk hw/sw inventory", "host status", "host check", "service check timed out",
    )):
        return "host_check"
    if any(token in text for token in ("check_mk", "check mk", "sensor", "monitoring", "monitoramento")):
        return "monitoring_sensor"
    return "other"


def policy_allows_autonomous_correction(event: dict[str, Any]) -> tuple[bool, str, str]:
    category = classify_problem_category(event)
    ensure_noc_policy_defaults()
    with SessionLocal() as session:
        row = session.scalar(
            select(NocAutomationPolicyORM).where(NocAutomationPolicyORM.category == category)
        )
    if row is None:
        return False, category, "categoria sem política configurada"
    if row.immutable or not row.enabled:
        return False, category, f"correção autônoma desabilitada para {row.label}"
    return True, category, f"categoria {row.label} habilitada para correção autônoma"


def _problem_payload(row: CheckmkProblemORM) -> dict[str, Any]:
    return {
        "problem_key": row.problem_key,
        "site_id": row.site_id,
        "client_alias": row.client_alias,
        "host": row.host_name,
        "host_address": row.internal_address,
        "service": row.service,
        "output": row.output,
        "skill_id": row.skill_id,
        "incident_id": row.incident_id,
        "job_id": row.job_id,
    }


def record_history_transition(
    event: dict[str, Any],
    *,
    status: str,
    reason: str = "",
    incident_id: str | None = None,
    job_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    ensure_database_schema()
    problem_key = str(event.get("problem_key") or "").strip() or None
    category = classify_problem_category(event)
    normalized_reason = redact_text(str(reason or ""))[:4000]
    with SessionLocal() as session:
        if problem_key:
            latest = session.scalar(
                select(NocActionHistoryORM)
                .where(NocActionHistoryORM.problem_key == problem_key)
                .order_by(NocActionHistoryORM.created_at.desc())
                .limit(1)
            )
            if latest and latest.status == status and latest.reason == normalized_reason:
                return
        session.add(
            NocActionHistoryORM(
                problem_key=problem_key,
                site_id=str(event.get("site_id") or "")[:64] or None,
                client_alias=str(event.get("alias") or event.get("client_alias") or "")[:255] or None,
                host_name=str(event.get("host") or event.get("host_name") or "")[:255] or None,
                internal_address=str(event.get("host_address") or event.get("internal_address") or "")[:64] or None,
                service=str(event.get("service") or "")[:512] or None,
                category=category,
                status=str(status or "detected")[:80],
                reason=normalized_reason,
                incident_id=str(incident_id or event.get("incident_id") or "")[:64] or None,
                job_id=str(job_id or event.get("job_id") or "")[:64] or None,
                metadata_payload=dict(metadata or {}),
            )
        )
        session.commit()


def record_incident_history(
    incident: dict[str, Any],
    *,
    status: str,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    ensure_database_schema()
    site_id = str(incident.get("site") or incident.get("site_id") or "")
    host = str(incident.get("host") or "")
    service = str(incident.get("service") or "")
    with SessionLocal() as session:
        conditions = [
            CheckmkProblemORM.host_name == host,
            CheckmkProblemORM.service == service,
        ]
        if site_id:
            conditions.append(CheckmkProblemORM.site_id == site_id)
        row = session.scalar(
            select(CheckmkProblemORM)
            .where(and_(*conditions))
            .order_by(CheckmkProblemORM.last_seen_at.desc())
            .limit(1)
        )
        event = _problem_payload(row) if row else {
            "site_id": site_id,
            "host": host,
            "service": service,
            "output": incident.get("last_output") or "",
            "incident_id": incident.get("id"),
            "job_id": incident.get("job_id"),
        }
    record_history_transition(
        event,
        status=status,
        reason=reason,
        incident_id=str(incident.get("id") or "") or None,
        job_id=str(incident.get("job_id") or "") or None,
        metadata=metadata,
    )


def list_noc_action_history(
    *,
    status: str | None = None,
    category: str | None = None,
    query: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    ensure_database_schema()
    limit = max(1, min(int(limit), 2000))
    with SessionLocal() as session:
        stmt = select(NocActionHistoryORM)
        if status:
            stmt = stmt.where(NocActionHistoryORM.status == str(status))
        if category:
            stmt = stmt.where(NocActionHistoryORM.category == str(category))
        rows = session.scalars(stmt.order_by(NocActionHistoryORM.created_at.desc()).limit(limit)).all()
    normalized = str(query or "").strip().casefold()
    items = []
    for row in rows:
        payload = {
            "id": str(row.id),
            "problem_key": row.problem_key,
            "site_id": row.site_id,
            "client_alias": row.client_alias,
            "host": row.host_name,
            "host_address": row.internal_address,
            "service": row.service,
            "category": row.category,
            "status": row.status,
            "reason": row.reason,
            "incident_id": row.incident_id,
            "job_id": row.job_id,
            "metadata": dict(row.metadata_payload or {}),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if normalized and normalized not in " ".join(
            str(payload.get(key) or "") for key in ("site_id", "client_alias", "host", "host_address", "service", "category", "status", "reason")
        ).casefold():
            continue
        items.append(payload)
    return {"total": len(items), "items": items}
