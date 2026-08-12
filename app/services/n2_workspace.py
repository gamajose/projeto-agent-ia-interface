from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import or_, select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.checkmk_master_models import CheckmkHostORM, CheckmkProblemORM, CheckmkSiteORM


# Estrutura baseada no template N2 fornecido. Campos de senha, community,
# secret, token e credenciais administrativas são deliberadamente excluídos.
# A Área N2 é um roteiro de coleta/documentação, não um cofre de segredos.
N2_TEMPLATE_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "identification",
        "title": "Identificação e responsáveis",
        "fields": [
            "cliente",
            "responsável infra",
            "responsável DBA",
            "responsável NOC",
            "revisão",
            "revisão NOC",
            "data",
        ],
    },
    {
        "id": "inventory",
        "title": "Inventário do ambiente",
        "fields": [
            "servidor de monitoramento",
            "servidor de aplicação",
            "servidor de banco de dados",
            "servidor de banco redundância",
            "servidor de banco teste",
        ],
    },
    {
        "id": "infrastructure",
        "title": "Informações de infraestrutura",
        "fields": [
            "servidor",
            "IPv4",
            "VPN",
            "hostname",
            "processador",
            "memória",
            "armazenamento",
            "sistema operacional",
        ],
    },
    {
        "id": "database",
        "title": "Banco de dados",
        "fields": ["SGBD", "versão Oracle/RDBMS", "instâncias"],
    },
    {
        "id": "totvs_activation",
        "title": "Informações para ativação na TOTVS",
        "fields": ["SID", "username técnico", "número de série"],
    },
    {
        "id": "sgdb_tnsnames",
        "title": "Estrutura SGDB e TNSNAMES",
        "fields": ["estrutura SGDB", "TNSNAMES"],
    },
    {
        "id": "winthor_mapping",
        "title": "Mapeamento do Winthor",
        "fields": ["compartilhamentos", "paths", "usuários técnicos sem senha", "origem autorizada"],
    },
    {
        "id": "backup_policy",
        "title": "Política de backup",
        "fields": ["estratégia", "cópias", "mídias", "off-site", "destinos"],
    },
    {
        "id": "oracle_backup",
        "title": "Backup Oracle",
        "fields": ["Datapump", "RMAN", "frequência", "início", "conclusão", "duração", "tamanho", "redundância"],
    },
    {
        "id": "erp_backup",
        "title": "Backup do sistema ERP / Winthor",
        "fields": ["frequência", "início", "conclusão", "duração", "tamanho", "cópia para redundância"],
    },
    {
        "id": "backup_execution",
        "title": "Métodos de execução e validações",
        "fields": ["backup lógico", "backup físico", "compactação", "HASH", "validação dos logs"],
    },
    {
        "id": "retention",
        "title": "Retenção de backups obsoletos",
        "fields": ["limite de uso", "quantidade mínima", "quantidade máxima", "timeline de retenção"],
    },
    {
        "id": "redundancy",
        "title": "Dados de redundância e standby",
        "fields": ["unidade local", "modelo/protocolo", "capacidade", "compartilhamento", "nuvem", "standby", "replicação"],
    },
    {
        "id": "monitoring",
        "title": "Monitoramento",
        "fields": ["site Checkmk", "endpoint", "hosts monitorados", "serviços", "estado atual", "painel"],
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


def _contains_any(value: str | None, tokens: tuple[str, ...]) -> bool:
    text = str(value or "").casefold()
    return any(token in text for token in tokens)


def _problem_matches(problem: dict[str, Any], tokens: tuple[str, ...]) -> bool:
    return _contains_any(
        " ".join(
            (
                str(problem.get("host") or ""),
                str(problem.get("service") or ""),
                str(problem.get("output") or ""),
            )
        ),
        tokens,
    )


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
    database_hosts = [
        item
        for item in host_payloads
        if _contains_any(item.get("host"), ("db", "oracle", "sql", "wint", "oda"))
        or str(item.get("kind") or "").casefold() == "database"
    ]
    standby_hosts = [
        item
        for item in host_payloads
        if item.get("environment") == "standby" or _contains_any(item.get("host"), ("standby", "stby", "dg"))
    ]
    monitor_hosts = [
        item
        for item in host_payloads
        if item.get("environment") == "monitoring" or item.get("kind") == "monitoring_local"
    ]
    application_hosts = [
        item
        for item in host_payloads
        if str(item.get("kind") or "").casefold() == "application"
        or _contains_any(item.get("host"), ("app", "aplic", "winthor", "totvs"))
    ]
    test_hosts = [
        item
        for item in host_payloads
        if item.get("environment") == "training" or _contains_any(item.get("host"), ("teste", "test", "hml", "homolog"))
    ]

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
            "application_hosts": application_hosts,
            "test_hosts": test_hosts,
        },
    }


def _append_alerts(known: list[str], problems: list[dict[str, Any]], tokens: tuple[str, ...]) -> None:
    for item in problems:
        if not _problem_matches(item, tokens):
            continue
        known.append(f"Alerta atual: {item['host']} / {item['service']} / {item['state']}")


def build_n2_documentation_draft(site_id: str, *, responsibles: dict[str, str] | None = None) -> dict[str, Any]:
    context = n2_site_context(site_id)
    if context is None:
        raise ValueError("site não encontrado")
    site = dict(context["site"])
    hosts = list(context["hosts"])
    problems = list(context["problems"])
    derived = dict(context["derived"])
    responsibles = {str(k): str(v or "").strip() for k, v in dict(responsibles or {}).items()}

    sections = []
    for definition in N2_TEMPLATE_SECTIONS:
        section_id = str(definition["id"])
        known: list[str] = []
        missing: list[str] = []

        if section_id == "identification":
            known.extend([
                f"Cliente: {site['alias']} ({site['site_id']})",
                f"Data: {date.today().isoformat()}",
            ])
            for key, label in (
                ("infra", "Responsável Infra"),
                ("dba", "Responsável DBA"),
                ("noc", "Responsável NOC"),
                ("review", "Revisão"),
                ("review_noc", "Revisão NOC"),
            ):
                if responsibles.get(key):
                    known.append(f"{label}: {responsibles[key]}")
                else:
                    missing.append(label)

        elif section_id == "inventory":
            groups = (
                ("Servidor de Monitoramento", derived.get("monitor_hosts") or []),
                ("Servidor de Aplicação", derived.get("application_hosts") or []),
                ("Servidor de Banco de Dados", derived.get("database_hosts") or []),
                ("Servidor de Banco de Dados Redundância", derived.get("standby_hosts") or []),
                ("Servidor de Banco de Dados Teste", derived.get("test_hosts") or []),
            )
            for label, rows in groups:
                if rows:
                    known.append(f"{label}: " + ", ".join(str(item.get("host") or "-") for item in rows))
                else:
                    missing.append(f"confirmar {label.casefold()}")

        elif section_id == "infrastructure":
            known.extend(
                f"{item['host']} | IPv4 {item.get('ip') or 'sem IP'} | papel {item.get('kind')} | ambiente {item.get('environment')}"
                for item in hosts
            )
            missing.extend([
                "processador por servidor",
                "memória por servidor",
                "armazenamento por servidor",
                "sistema operacional por servidor",
                "Address VPN quando diferente do endpoint do cliente",
            ])

        elif section_id == "database":
            if derived.get("database_hosts"):
                known.extend(
                    f"Host candidato de banco: {item['host']} ({item.get('ip') or 'sem IP'})"
                    for item in derived["database_hosts"]
                )
            _append_alerts(known, problems, ("oracle", "mssql", "sqlserver", "postgres", "database", "db2", "mysql"))
            missing.extend(["SGBD confirmado", "versão Oracle/RDBMS", "instâncias"])

        elif section_id == "totvs_activation":
            if derived.get("database_hosts"):
                known.append("Há host(s) candidato(s) de banco para validar dados TOTVS.")
            missing.extend(["SID", "username técnico", "número de série"])

        elif section_id == "sgdb_tnsnames":
            missing.extend(["estrutura SGDB", "TNSNAMES validado"])

        elif section_id == "winthor_mapping":
            if derived.get("application_hosts"):
                known.extend(
                    f"Host candidato de aplicação/Winthor: {item['host']} ({item.get('ip') or 'sem IP'})"
                    for item in derived["application_hosts"]
                )
            missing.extend([
                "compartilhamento winthor somente leitura",
                "compartilhamento administrativo",
                "paths dos compartilhamentos",
                "origem/IP autorizado",
                "usuários técnicos sem registrar senha",
            ])

        elif section_id == "backup_policy":
            _append_alerts(known, problems, ("backup", "rman", "datapump", "archive", "winthor"))
            missing.extend([
                "estratégia 3-2-1 ou redundante",
                "quantidade de cópias",
                "mídias/destinos",
                "cópia off-site",
            ])

        elif section_id == "oracle_backup":
            _append_alerts(known, problems, ("rman", "datapump", "archive", "archivelog", "oracle backup"))
            missing.extend([
                "frequência Datapump",
                "frequência RMAN",
                "horário de início/conclusão",
                "duração",
                "tamanho",
                "cópia para redundância",
            ])

        elif section_id == "erp_backup":
            _append_alerts(known, problems, ("winthor", "erp backup", "backup sistema"))
            missing.extend(["frequência", "horário início/conclusão", "duração", "tamanho", "redundância"])

        elif section_id == "backup_execution":
            _append_alerts(known, problems, ("datapump", "rman", "hash", "tar", "backup"))
            missing.extend([
                "fluxo do backup lógico",
                "fluxo do backup físico",
                "compactação/compressão",
                "validação HASH",
                "logs/sensores usados na validação",
            ])

        elif section_id == "retention":
            missing.extend([
                "percentual limite de uso do disco",
                "quantidade mínima de backups",
                "quantidade máxima de backups",
                "timeline/regra de remoção dos backups obsoletos",
            ])

        elif section_id == "redundancy":
            known.extend(
                f"Standby identificado: {item['host']} ({item.get('ip') or 'sem IP'})"
                for item in derived.get("standby_hosts") or []
            )
            _append_alerts(known, problems, ("standby", "dataguard", "replication", "redund", "sync"))
            missing.extend([
                "unidade de redundância local",
                "modelo/protocolo",
                "capacidade",
                "compartilhamento sem senha",
                "redundância em nuvem/DR",
                "intervalo/estado da replicação",
            ])

        elif section_id == "monitoring":
            known.extend([
                f"Site Checkmk: {site['site_id']}",
                f"Endpoint Livestatus: {site.get('endpoint') or '-'}:{site.get('port') or '-'}",
                f"Hosts monitorados: {len(hosts)}",
                f"Problemas ativos: {len(problems)}",
            ])
            if site.get("status_host"):
                known.append(f"Status host: {site['status_host']}")
            missing.append("URL/usuário do painel, se necessário; nunca registrar senha")

        else:
            missing.extend(["escopo validado", "pendências finais", "responsáveis", "evidências anexas"])

        sections.append(
            {
                "id": section_id,
                "title": definition["title"],
                "fields": list(definition["fields"]),
                "known": known,
                "missing": missing,
                "status": "partial" if known and missing else "ready" if known else "pending",
            }
        )

    return {
        "title": f"Documentação N2 — {site['alias']}",
        "site_id": site["site_id"],
        "client": site["alias"],
        "generated_from": "CMK05/master + inventário Checkmk persistido",
        "template_profile": "template_teste — inventário, banco/TOTVS, Winthor, backup, retenção, redundância e monitoramento",
        "sections": sections,
        "hosts": hosts,
        "problems": problems,
        "security": {
            "credentials_included": False,
            "rule": "Senhas, communities, tokens e secrets não entram no rascunho nem no prompt da IA.",
        },
        "ai_guidance": [
            "Preencher somente informações sustentadas por evidência coletada.",
            "Não inferir versão de banco, estratégia de backup, retenção ou redundância sem coleta específica.",
            "Usar os itens pendentes como roteiro para investigações N2 adicionais.",
            "Acesso a banco continua bloqueado; validar software/serviços por evidências externas quando possível.",
            "Nunca executar reboot, shutdown, poweroff ou halt do servidor durante coleta, correção ou validação.",
        ],
    }
