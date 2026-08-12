from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import or_, select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkProblemORM, CheckmkSiteORM


# Estrutura inspirada no template operacional enviado pelo usuário. Campos de
# senha/credencial são deliberadamente excluídos: o workspace nunca deve
# materializar segredos em documentação ou prompt de IA.
N2_TEMPLATE_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "identification",
        "title": "Identificação e responsáveis",
        "fields": ["cliente", "responsável infra", "responsável DBA", "responsável NOC", "revisão", "data"],
    },
    {
        "id": "infrastructure",
        "title": "Infraestrutura e inventário",
        "fields": ["servidores", "IPv4", "VPN", "hostname", "papel", "sistema operacional", "processador", "memória", "armazenamento"],
    },
    {
        "id": "database",
        "title": "Banco de dados",
        "fields": ["SGBD", "versão", "instâncias", "estrutura", "TNSNAMES", "dados para ativação TOTVS"],
    },
    {
        "id": "backup",
        "title": "Política e validação de backup",
        "fields": ["estratégia", "Datapump", "RMAN", "Winthor", "frequência", "horários", "retenção", "redundância"],
    },
    {
        "id": "redundancy",
        "title": "Redundância e standby",
        "fields": ["unidade local", "nuvem", "standby", "replicação", "sincronização"],
    },
    {
        "id": "monitoring",
        "title": "Monitoramento",
        "fields": ["site Checkmk", "endpoint", "hosts monitorados", "serviços", "estado atual", "acesso ao painel"],
    },
    {
        "id": "closing",
        "title": "Considerações finais",
        "fields": ["escopo validado", "pendências", "responsáveis", "evidências"],
    },
)


def _host_payload(row: CheckmkHostORM) -> dict[str, Any]:
    return {
        "host": row.host_name,
        "ip": row.internal_address,
        "state": row.state,
        "environment": row.environment,
        "kind": row.host_kind,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def list_n2_sites(*, query: str | None = None, limit: int = 500) -> dict[str, Any]:
    ensure_database_schema()
    limit = max(1, min(int(limit), 1000))
    with SessionLocal() as session:
        stmt = select(CheckmkSiteORM).where(CheckmkSiteORM.enabled.is_(True))
        normalized = str(query or "").strip()
        if normalized:
            pattern = f"%{normalized}%"
            stmt = stmt.where(or_(CheckmkSiteORM.site_id.ilike(pattern), CheckmkSiteORM.alias.ilike(pattern)))
        rows = session.scalars(stmt.order_by(CheckmkSiteORM.alias).limit(limit)).all()
    return {
        "items": [
            {
                "site_id": row.site_id,
                "alias": row.alias,
                "hosts": row.host_count,
                "problems": row.problem_count,
                "endpoint": row.livestatus_host,
                "port": row.livestatus_port,
                "status_host": row.status_host,
                "shared_endpoint": row.shared_endpoint,
                "last_polled_at": row.last_polled_at.isoformat() if row.last_polled_at else None,
            }
            for row in rows
        ]
    }


def n2_site_context(site_id: str) -> dict[str, Any] | None:
    ensure_database_schema()
    with SessionLocal() as session:
        site = session.scalar(select(CheckmkSiteORM).where(CheckmkSiteORM.site_id == str(site_id)))
        if site is None:
            return None
        hosts = session.scalars(
            select(CheckmkHostORM).where(CheckmkHostORM.site_id == site.site_id).order_by(CheckmkHostORM.host_name)
        ).all()
        problems = session.scalars(
            select(CheckmkProblemORM)
            .where(CheckmkProblemORM.site_id == site.site_id, CheckmkProblemORM.active.is_(True))
            .order_by(CheckmkProblemORM.state.desc(), CheckmkProblemORM.host_name)
        ).all()

    host_payloads = [_host_payload(row) for row in hosts]
    database_hosts = [item for item in host_payloads if any(token in str(item["host"]).casefold() for token in ("db", "oracle", "sql"))]
    standby_hosts = [item for item in host_payloads if item.get("environment") == "standby" or "standby" in str(item["host"]).casefold()]
    monitor_hosts = [item for item in host_payloads if item.get("environment") == "monitoring" or item.get("kind") == "monitoring_local"]

    return {
        "site": {
            "site_id": site.site_id,
            "alias": site.alias,
            "endpoint": site.livestatus_host,
            "port": site.livestatus_port,
            "status_host": site.status_host,
            "shared_endpoint": site.shared_endpoint,
            "last_polled_at": site.last_polled_at.isoformat() if site.last_polled_at else None,
        },
        "hosts": host_payloads,
        "problems": [
            {
                "host": row.host_name,
                "ip": row.internal_address,
                "service": row.service,
                "state": row.state_name,
                "output": row.output,
            }
            for row in problems
        ],
        "derived": {
            "database_hosts": database_hosts,
            "standby_hosts": standby_hosts,
            "monitor_hosts": monitor_hosts,
        },
    }


def build_n2_documentation_draft(site_id: str, *, responsibles: dict[str, str] | None = None) -> dict[str, Any]:
    context = n2_site_context(site_id)
    if context is None:
        raise ValueError("site não encontrado")
    site = dict(context["site"])
    hosts = list(context["hosts"])
    derived = dict(context["derived"])
    responsibles = {str(k): str(v or "").strip() for k, v in dict(responsibles or {}).items()}

    sections = []
    for definition in N2_TEMPLATE_SECTIONS:
        section_id = str(definition["id"])
        known: list[str] = []
        missing: list[str] = []
        if section_id == "identification":
            known.append(f"Cliente: {site['alias']} ({site['site_id']})")
            known.append(f"Data: {date.today().isoformat()}")
            for key, label in (("infra", "Responsável Infra"), ("dba", "Responsável DBA"), ("noc", "Responsável NOC"), ("review", "Revisão")):
                if responsibles.get(key):
                    known.append(f"{label}: {responsibles[key]}")
                else:
                    missing.append(label)
        elif section_id == "infrastructure":
            known.extend(
                f"{item['host']} | {item.get('ip') or 'sem IP'} | {item.get('kind')} | {item.get('environment')}"
                for item in hosts
            )
            missing.extend(["processador", "memória", "armazenamento", "sistema operacional"])
        elif section_id == "database":
            if derived.get("database_hosts"):
                known.extend(f"Host candidato de banco: {item['host']} ({item.get('ip') or 'sem IP'})" for item in derived["database_hosts"])
            missing.extend(["SGBD", "versão", "instâncias", "estrutura SGDB", "TNSNAMES", "dados TOTVS"])
        elif section_id == "backup":
            backup_alerts = [item for item in context["problems"] if any(token in str(item.get("service") or "").casefold() for token in ("backup", "rman", "datapump", "archive", "winthor"))]
            known.extend(f"Alerta atual: {item['host']} / {item['service']} / {item['state']}" for item in backup_alerts)
            missing.extend(["estratégia de backup", "frequências", "horários", "retenção", "destinos de redundância"])
        elif section_id == "redundancy":
            known.extend(f"Standby identificado: {item['host']} ({item.get('ip') or 'sem IP'})" for item in derived.get("standby_hosts") or [])
            missing.extend(["unidade de redundância", "nuvem", "intervalos de sincronização"])
        elif section_id == "monitoring":
            known.extend([
                f"Site Checkmk: {site['site_id']}",
                f"Endpoint Livestatus: {site.get('endpoint') or '-'}:{site.get('port') or '-'}",
                f"Hosts monitorados: {len(hosts)}",
                f"Problemas ativos: {len(context['problems'])}",
            ])
            if site.get("status_host"):
                known.append(f"Status host: {site['status_host']}")
            missing.append("URL/usuário do painel, se a documentação exigir")
        else:
            missing.extend(["escopo validado", "pendências finais", "evidências anexas"])

        sections.append({
            "id": section_id,
            "title": definition["title"],
            "fields": list(definition["fields"]),
            "known": known,
            "missing": missing,
            "status": "partial" if known and missing else "ready" if known else "pending",
        })

    return {
        "title": f"Documentação N2 — {site['alias']}",
        "site_id": site["site_id"],
        "client": site["alias"],
        "generated_from": "CMK05/master + inventário Checkmk persistido",
        "sections": sections,
        "hosts": hosts,
        "problems": context["problems"],
        "security": {
            "credentials_included": False,
            "rule": "Senhas, communities e secrets não entram no rascunho nem no prompt da IA.",
        },
        "ai_guidance": [
            "Preencher somente informações sustentadas por evidência coletada.",
            "Não inferir versão de banco, estratégia de backup, retenção ou redundância sem coleta específica.",
            "Usar os itens pendentes como roteiro para investigações N2 adicionais.",
            "Nunca executar reboot do servidor durante coleta ou validação.",
        ],
    }
