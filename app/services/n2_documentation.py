from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import Any, Iterable

from app.services.n2_workspace import n2_site_context
from app.services.ui_executions import execution_detail


MAX_RELATED_HOSTS_PER_EXECUTION = 3
_ALLOWED_ROLES = {"monitoring", "production", "standby", "database", "application", "firewall", "other"}


_FIELD_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "database",
        "title": "Banco de dados / TOTVS / TNSNAMES",
        "fields": (
            ("rdbms_version", "Versão Oracle / RDBMS", "text"),
            ("instances", "Instâncias", "text"),
            ("sid", "SID", "text"),
            ("totvs_username", "Username técnico TOTVS", "text"),
            ("serial_number", "Número de série TOTVS", "text"),
            ("sgdb_structure", "Estrutura SGDB", "textarea"),
            ("tnsnames", "TNSNAMES", "textarea"),
            ("database_notes", "Evidências / observações de banco", "textarea"),
        ),
    },
    {
        "id": "winthor",
        "title": "Mapeamento do Winthor",
        "fields": (
            ("winthor_user_admin", "Usuário técnico administrativo", "text"),
            ("winthor_admin_path", "Path winthor-adm", "text"),
            ("winthor_user_read", "Usuário técnico somente leitura", "text"),
            ("winthor_read_path", "Path winthor", "text"),
            ("winthor_authorized_origin", "Origem / IP autorizado", "text"),
            ("winthor_notes", "Evidências / observações Winthor", "textarea"),
        ),
    },
    {
        "id": "backup",
        "title": "Backup Oracle / ERP e métodos de validação",
        "fields": (
            ("backup_strategy", "Estratégia de backup", "textarea"),
            ("datapump_frequency", "Datapump - frequência", "text"),
            ("datapump_start", "Datapump - início", "text"),
            ("datapump_end", "Datapump - conclusão", "text"),
            ("datapump_duration", "Datapump - duração", "text"),
            ("datapump_size", "Datapump - tamanho", "text"),
            ("datapump_redundancy", "Datapump - redundância", "text"),
            ("rman_frequency", "RMAN - frequência", "text"),
            ("rman_type", "RMAN - tipo", "text"),
            ("rman_start", "RMAN - início", "text"),
            ("rman_end", "RMAN - conclusão", "text"),
            ("rman_duration", "RMAN - duração", "text"),
            ("rman_size", "RMAN - tamanho", "text"),
            ("rman_redundancy", "RMAN - redundância", "text"),
            ("archives_frequency", "Archives - frequência", "text"),
            ("archives_duration", "Archives - duração", "text"),
            ("archives_size_min", "Archives - tamanho mínimo", "text"),
            ("archives_size_max", "Archives - tamanho máximo", "text"),
            ("archives_redundancy", "Archives - redundância", "text"),
            ("winthor_backup_frequency", "Winthor - frequência", "text"),
            ("winthor_backup_start", "Winthor - início", "text"),
            ("winthor_backup_end", "Winthor - conclusão", "text"),
            ("winthor_backup_duration", "Winthor - duração", "text"),
            ("winthor_backup_size", "Winthor - tamanho", "text"),
            ("winthor_backup_redundancy", "Winthor - redundância", "text"),
            ("logical_backup_method", "Método / validação do backup lógico", "textarea"),
            ("physical_backup_method", "Método / validação do backup físico", "textarea"),
            ("backup_execution_notes", "Outras evidências de backup", "textarea"),
        ),
    },
    {
        "id": "retention",
        "title": "Retenção de backups",
        "fields": (
            ("local_backup_path", "Caminho de backup local", "text"),
            ("rman_local_dir", "Diretório RMAN local", "text"),
            ("datapump_local_dir", "Diretório Datapump local", "text"),
            ("datapump_local_threshold", "Limite de disco Datapump local", "text"),
            ("datapump_local_min", "Mínimo Datapump local", "text"),
            ("datapump_local_max", "Máximo Datapump local", "text"),
            ("winthor_local_dir", "Diretório Winthor local", "text"),
            ("redundancy_backup_path", "Caminho da unidade de redundância", "text"),
            ("rman_redundancy_dir", "Diretório RMAN redundância", "text"),
            ("datapump_redundancy_dir", "Diretório Datapump redundância", "text"),
            ("winthor_redundancy_dir", "Diretório Winthor redundância", "text"),
            ("retention_notes", "Regra / timeline de retenção", "textarea"),
        ),
    },
    {
        "id": "redundancy",
        "title": "Redundância e standby",
        "fields": (
            ("redundancy_type", "Tipo da unidade de redundância", "text"),
            ("redundancy_model", "Modelo / protocolo", "text"),
            ("redundancy_capacity", "Capacidade", "text"),
            ("redundancy_share", "Compartilhamento", "text"),
            ("redundancy_user", "Usuário técnico", "text"),
            ("cloud_redundancy", "Redundância em nuvem / DR", "textarea"),
            ("standby_db_sync", "Replicação standby - banco", "textarea"),
            ("standby_winthor_sync", "Replicação standby - Winthor", "textarea"),
            ("redundancy_notes", "Evidências / observações de redundância", "textarea"),
        ),
    },
    {
        "id": "monitoring",
        "title": "Monitoramento",
        "fields": (
            ("monitoring_url", "URL do painel", "text"),
            ("monitoring_user", "Usuário do painel", "text"),
            ("monitoring_site", "Site Checkmk", "text"),
            ("monitoring_endpoint", "Endpoint", "text"),
            ("monitoring_host_count", "Hosts monitorados", "text"),
            ("monitoring_problem_count", "Problemas ativos na coleta", "text"),
            ("monitoring_notes", "Evidências / observações de monitoramento", "textarea"),
        ),
    },
    {
        "id": "closing",
        "title": "Considerações finais",
        "fields": (("closing_notes", "Considerações finais", "textarea"),),
    },
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _role(host: dict[str, Any]) -> str:
    kind = _text(host.get("kind")).casefold()
    environment = _text(host.get("environment")).casefold()
    name = _text(host.get("host")).casefold()
    if kind in {"bmc", "ilo", "idrac", "ilom"}:
        return "other"
    if kind == "firewall":
        return "firewall"
    if kind == "monitoring_local" or environment == "monitoring":
        return "monitoring"
    if environment == "standby" or "standby" in name or "stby" in name:
        return "standby"
    if environment == "production":
        return "production"
    if kind == "database" or any(token in name for token in ("db", "oracle", "sql", "oda")):
        return "database"
    if kind == "application" or any(token in name for token in ("app", "winthor", "totvs")):
        return "application"
    return "other"


def _environment(host: dict[str, Any]) -> str:
    value = _text(host.get("environment")).casefold()
    if value in {"production", "standby", "monitoring", "training", "unknown"}:
        return value
    role = _role(host)
    if role == "monitoring":
        return "monitoring"
    if role == "production":
        return "production"
    if role == "standby":
        return "standby"
    return "unknown"


def _is_internal_target(host: dict[str, Any], endpoint: str) -> bool:
    address = _text(host.get("ip"))
    return bool(address and address not in {"0.0.0.0", "127.0.0.1", "::1"} and address != endpoint)


def _selected(context: dict[str, Any], host_names: Iterable[str]) -> list[dict[str, Any]]:
    requested = {_text(item).casefold() for item in host_names if _text(item)}
    if not requested:
        raise ValueError("selecione pelo menos um host para a validação N2")
    rows = [dict(item) for item in context.get("hosts") or [] if _text(item.get("host")).casefold() in requested]
    found = {_text(item.get("host")).casefold() for item in rows}
    missing = sorted(requested - found)
    if missing:
        raise ValueError("host(s) não pertencem ao cliente/site selecionado: " + ", ".join(missing))
    return rows


def _collection_objective(*, client: str, site_id: str, selected_hosts: list[dict[str, Any]], batch_hosts: list[dict[str, Any]]) -> str:
    selected_labels = ", ".join(_text(item.get("host")) for item in selected_hosts)
    batch_labels = ", ".join(_text(item.get("host")) for item in batch_hosts) or "host de entrada/monitoramento"
    return f"""VALIDAÇÃO DOCUMENTAL N2 — SOMENTE LEITURA
Cliente: {client}
Site Checkmk: {site_id}
Hosts escolhidos pelo analista: {selected_labels}
Hosts deste lote: {batch_labels}

Objetivo: coletar evidências factuais para preencher a documentação N2 no padrão 2Com. Faça uma validação ampla, porém estritamente somente leitura, cobrindo quando aplicável ao host atual:
- inventário e infraestrutura: hostname, IPv4, SO, CPU, memória, discos/filesystems, mounts e papel do servidor;
- software de banco sem autenticar no banco: versão Oracle/RDBMS, ORACLE_HOME, instâncias/SID observáveis por processos/configuração, estrutura de arquivos e TNSNAMES; nunca use sqlplus, rman conectado, psql, mysql ou outro cliente de banco;
- dados TOTVS que sejam não secretos e visíveis por configuração/arquivo permitido, como SID, username técnico e número de série; não procure nem exponha senha;
- Winthor: compartilhamentos, paths, usuários técnicos e origem autorizada, sem coletar credenciais;
- backup: estratégia/configurações, Datapump, RMAN, archives, Winthor/ERP, frequência, horários, duração, tamanho, destinos, logs, HASH e validações, somente por arquivos/processos/logs permitidos;
- retenção: diretórios, limites, quantidades mínima/máxima e regras de limpeza, sem excluir nada;
- redundância/standby: mounts/NAS/compartilhamentos, capacidade/protocolo quando observável, nuvem/DR por configuração local e sincronização de banco/Winthor;
- monitoramento: Checkmk/OMD, site, serviços/sensores e informações úteis para documentação.

REGRAS ABSOLUTAS:
1. NUNCA executar reboot, shutdown, poweroff, halt, init 0/6 ou qualquer reinicialização/desligamento do servidor.
2. NUNCA reiniciar, parar, habilitar/desabilitar ou alterar serviços durante esta validação documental.
3. NUNCA alterar arquivos, permissões, pacotes, firewall, rede, banco, backup ou configuração.
4. NUNCA acessar banco de dados do cliente por cliente SQL/RMAN nem executar comandos de escrita.
5. NUNCA coletar, imprimir, registrar ou inferir senha, community SNMP, token, secret, chave privada ou credencial.
6. Se um dado não puder ser confirmado por evidência segura, marque como não confirmado; não invente.
7. Registre evidências e saídas suficientes para que o analista N2 possa revisar e editar os campos antes da exportação.
""".strip()


def build_n2_collection_plan(site_id: str, host_names: Iterable[str]) -> dict[str, Any]:
    context = n2_site_context(site_id)
    if context is None:
        raise ValueError("cliente/site não encontrado")
    site = dict(context.get("site") or {})
    endpoint = _text(site.get("endpoint"))
    if not endpoint:
        raise ValueError("o cliente/site não possui endpoint de acesso conhecido")
    selected_hosts = _selected(context, host_names)
    internal = [item for item in selected_hosts if _is_internal_target(item, endpoint)]
    primary_selected = any(
        _text(item.get("ip")) in {endpoint, "0.0.0.0", ""} and _role(item) == "monitoring"
        for item in selected_hosts
    )
    batches: list[dict[str, Any]] = []
    chunks = [internal[i : i + MAX_RELATED_HOSTS_PER_EXECUTION] for i in range(0, len(internal), MAX_RELATED_HOSTS_PER_EXECUTION)]
    if not chunks:
        chunks = [[]]
    for index, chunk in enumerate(chunks, start=1):
        related = []
        for host in chunk:
            role = _role(host)
            if role not in _ALLOWED_ROLES:
                role = "other"
            related.append(
                {
                    "reference": _text(host.get("ip")),
                    "ssh_port": int(host.get("ssh_port") or 22),
                    "role": role,
                    "environment": _environment(host),
                    "label": _text(host.get("host")),
                    "route_type": "ssh",
                    "credential_ref": "SSH_DEFAULT_PASSWORD",
                }
            )
        batch_hosts = chunk or ([item for item in selected_hosts if _role(item) == "monitoring"] if primary_selected else [])
        batches.append(
            {
                "index": index,
                "target": endpoint,
                "ssh_port": 22,
                "related_targets": related,
                "host_names": [_text(item.get("host")) for item in batch_hosts],
                "objective": _collection_objective(
                    client=_text(site.get("alias") or site_id),
                    site_id=_text(site.get("site_id") or site_id),
                    selected_hosts=selected_hosts,
                    batch_hosts=batch_hosts,
                ),
            }
        )
    return {
        "site": site,
        "selected_hosts": [
            {**item, "role": _role(item), "environment": _environment(item), "ssh_port": int(item.get("ssh_port") or 22)}
            for item in selected_hosts
        ],
        "batches": batches,
        "limits": {"max_related_hosts_per_execution": MAX_RELATED_HOSTS_PER_EXECUTION},
        "safety": {
            "read_only": True,
            "server_reboot": "absolute_denial",
            "service_changes": "denied_for_n2_collection",
            "database_clients": "denied",
            "secrets": "never_collect",
        },
    }


def _flatten_execution(execution_ids: Iterable[str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    results: list[dict[str, Any]] = []
    completed: list[str] = []
    errors: list[str] = []
    for execution_id in execution_ids:
        identifier = _text(execution_id)
        if not identifier:
            continue
        record = execution_detail(identifier)
        if not record:
            errors.append(f"Execução {identifier}: não encontrada ou expirada.")
            continue
        status = _text(record.get("status")).casefold()
        if status != "completed":
            errors.append(f"Execução {identifier}: status {status or 'desconhecido'}.")
            continue
        result = record.get("result")
        if isinstance(result, dict):
            results.append(dict(result))
            completed.append(identifier)
        else:
            errors.append(f"Execução {identifier}: concluída sem resultado utilizável.")
    return results, completed, errors


def _evidence_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for raw in result.get("evidence") or []:
            if isinstance(raw, dict):
                rows.append(dict(raw))
    return rows


def _output(item: dict[str, Any]) -> str:
    for value in (item.get("stdout"), item.get("output"), item.get("stdout_tail"), item.get("raw"), item.get("normalized"), item.get("result")):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (dict, list)) and value:
            return str(value)
    return ""


def _command(item: dict[str, Any]) -> str:
    return " ".join(_text(item.get(key)) for key in ("tool", "command", "purpose") if _text(item.get(key)))


def _host_matches(item: dict[str, Any], host: dict[str, Any]) -> bool:
    names = {_text(host.get("host")).casefold(), _text(host.get("ip")).casefold(), _text((host.get("fields") or {}).get("hostname")).casefold()}
    sources = {_text(item.get("source_hostname")).casefold(), _text(item.get("source_host")).casefold(), _text(item.get("host")).casefold()}
    return bool((names - {""}) & (sources - {""}))


def _first_match(text: str, patterns: Iterable[str], flags: int = re.I | re.M) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            value = _text(match.group(1))
            if value:
                return value
    return ""


def _host_fields(host: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[dict[str, str], str]:
    host_evidence = [item for item in evidence if _host_matches(item, host)]
    combined = "\n\n".join(f"{_command(item)}\n{_output(item)}" for item in host_evidence if _output(item))
    hostname = _first_match(combined, (r"(?:^|\n)hostname\s*[:=]?\s*([^\s\n]+)", r"(?:Static hostname|Hostname):\s*([^\s\n]+)")) or _text(host.get("host"))
    os_name = _first_match(combined, (r'^PRETTY_NAME=["\']?([^"\'\n]+)', r"Operating System:\s*([^\n]+)", r"(?:^|\n)OS:\s*([^\n]+)"))
    processor = _first_match(combined, (r"Model name:\s*([^\n]+)", r"Architecture:\s*([^\n]+)", r"(?:^|\n)CPU(?:\(s\))?:\s*([^\n]+)"))
    memory = _first_match(combined, (r"(?:^|\n)Mem:\s*([0-9.,]+\s*[KMGTPE]i?B?)", r"MemTotal:\s*([^\n]+)", r"Total Memory:\s*([^\n]+)"))
    storage = _first_match(combined, (r"(?:^|\n)(/dev/\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+/)$", r"(?:^|\n)Filesystem\s+Size\s+Used\s+Avail\s+Use%\s+Mounted on\s*\n([^\n]+)", r"(?:^|\n)root(?: filesystem)?\s*[:=]\s*([^\n]+)"))
    fields = {
        "server": _text(host.get("host")), "address_ipv4": _text(host.get("ip")), "address_vpn": "",
        "hostname": hostname, "processor": processor, "memory": memory, "storage": storage, "os": os_name,
    }
    snippets = []
    for item in host_evidence[:16]:
        value = _output(item)
        if value:
            snippets.append(f"{_command(item) or 'Coleta'}: {value[:650]}")
    return fields, "\n".join(snippets)[:8000]


def _all_text(results: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for result in results:
        analysis = result.get("analysis") or {}
        if isinstance(analysis, dict):
            for key in ("summary", "probable_cause", "conclusion", "next_safe_step"):
                if _text(analysis.get(key)):
                    chunks.append(_text(analysis.get(key)))
            chunks.extend(_text(item) for item in analysis.get("facts") or [] if _text(item))
        multi = result.get("multi_host") or {}
        if isinstance(multi, dict):
            for host in multi.get("hosts") or []:
                if isinstance(host, dict):
                    chunks.extend(_text(host.get(key)) for key in ("summary", "probable_cause", "conclusion") if _text(host.get(key)))
                    chunks.extend(_text(item) for item in host.get("facts") or [] if _text(item))
    for item in evidence:
        output = _output(item)
        if output:
            chunks.append(f"{_command(item)}\n{output}")
    return "\n\n".join(chunks)


def _note(text: str, tokens: tuple[str, ...], *, max_lines: int = 14) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        lower = line.casefold()
        if line and any(token in lower for token in tokens):
            if not any(secret in lower for secret in ("password=", "senha=", "community=", "token=", "secret=")):
                lines.append(line[:900])
        if len(lines) >= max_lines:
            break
    return "\n".join(dict.fromkeys(lines))


def _field_value_map(context: dict[str, Any], text: str) -> dict[str, str]:
    site = dict(context.get("site") or {})
    values = {key: "" for group in _FIELD_GROUPS for key, _label, _type in group["fields"]}
    values.update({
        "monitoring_site": _text(site.get("site_id")),
        "monitoring_endpoint": f"{_text(site.get('endpoint'))}:{_text(site.get('port'))}".rstrip(":"),
        "monitoring_host_count": str(len(context.get("hosts") or [])),
        "monitoring_problem_count": str(len(context.get("problems") or [])),
    })
    values["rdbms_version"] = _first_match(text, (r"Oracle Database\s+([^\n]+)", r"Oracle\s+(?:Database\s+)?(?:version|release)\s*[:=]?\s*([^\n]+)", r"RDBMS\s*[:=]\s*([^\n]+)"))
    values["instances"] = _first_match(text, (r"(?:ORACLE_SID|SID)\s*[:=]\s*([A-Za-z0-9_,.-]+)", r"(?:instance|instância|instancia)\s*[:=]\s*([A-Za-z0-9_,. -]+)"))
    values["sid"] = _first_match(text, (r"(?:ORACLE_SID|SID)\s*[:=]\s*([A-Za-z0-9_.-]+)",))
    values["tnsnames"] = _note(text, ("tnsnames", "description=", "address=", "service_name"), max_lines=24)
    values["database_notes"] = _note(text, ("oracle", "rdbms", "ora_", "tnsnames", "listener", "instance", "instância", "sid"), max_lines=20)
    values["winthor_admin_path"] = _first_match(text, (r"(\\\\[^\s\n]+\\winthor-adm[^\s\n]*)", r"(\/[^\s\n]*winthor-adm[^\s\n]*)"))
    values["winthor_read_path"] = _first_match(text, (r"(\\\\[^\s\n]+\\winthor(?:\s|$))", r"(\/[^\s\n]*winthor(?:\s|$))"))
    values["winthor_notes"] = _note(text, ("winthor", "samba", "smb", "share", "compartilh"), max_lines=18)
    values["backup_strategy"] = _note(text, ("backup 3-2-1", "3-2-1", "backup redund", "off-site", "offsite"), max_lines=8)
    values["logical_backup_method"] = _note(text, ("datapump", "expdp", "dump", "tar", "hash"), max_lines=18)
    values["physical_backup_method"] = _note(text, ("rman", "archivelog", "archive", "hash"), max_lines=18)
    values["backup_execution_notes"] = _note(text, ("backup", "datapump", "expdp", "rman", "archive", "winthor", "hash"), max_lines=24)
    values["datapump_frequency"] = _first_match(text, (r"(?:datapump|expdp).{0,80}(?:frequ[eê]ncia|schedule|cron)\s*[:=]?\s*([^\n]+)",))
    values["rman_frequency"] = _first_match(text, (r"rman.{0,80}(?:frequ[eê]ncia|schedule|cron)\s*[:=]?\s*([^\n]+)",))
    values["archives_frequency"] = _first_match(text, (r"archive(?:log|s)?.{0,80}(?:frequ[eê]ncia|schedule|cron)\s*[:=]?\s*([^\n]+)",))
    values["winthor_backup_frequency"] = _first_match(text, (r"winthor.{0,80}(?:backup).{0,80}(?:frequ[eê]ncia|schedule|cron)\s*[:=]?\s*([^\n]+)",))
    values["local_backup_path"] = _first_match(text, (r"(?:backup|rman|datapump).{0,40}(\/[^\s\n]+)",))
    values["retention_notes"] = _note(text, ("retention", "retenção", "retencao", "mínimo", "minimo", "máximo", "maximo", "90%", "cleanup", "limpeza"), max_lines=22)
    values["redundancy_notes"] = _note(text, ("redund", "standby", "dataguard", "data guard", "nas", "nfs", "cifs", "mount", "sync", "replica"), max_lines=22)
    values["standby_db_sync"] = _note(text, ("standby", "dataguard", "data guard", "archive applied", "apply lag", "transport lag"), max_lines=14)
    values["standby_winthor_sync"] = _note(text, ("sync_winthor", "winthor", "rsync"), max_lines=12)
    values["cloud_redundancy"] = _note(text, ("oracle cloud", "oci", "cloud", "nuvem", "disaster recovery", " dr "), max_lines=12)
    values["redundancy_type"] = "NAS" if re.search(r"\bnas\b", text, re.I) else ""
    values["redundancy_model"] = "NFS" if re.search(r"\bnfs\b", text, re.I) else "CIFS/SMB" if re.search(r"\b(cifs|smb)\b", text, re.I) else ""
    values["monitoring_notes"] = _note(text, ("checkmk", "check_mk", "omd", "livestatus", "nagios", "monitor"), max_lines=20)
    values["closing_notes"] = "Documentação gerada a partir das evidências coletadas nos hosts selecionados. Revise os campos abaixo antes de exportar. Campos sem evidência permanecem em branco."
    return values


def build_n2_review(site_id: str, host_names: Iterable[str], *, responsibles: dict[str, str] | None = None, execution_ids: Iterable[str] = ()) -> dict[str, Any]:
    context = n2_site_context(site_id)
    if context is None:
        raise ValueError("cliente/site não encontrado")
    selected_hosts = _selected(context, host_names)
    results, completed, errors = _flatten_execution(execution_ids)
    evidence = _evidence_rows(results)
    all_text = _all_text(results, evidence)
    values = _field_value_map(context, all_text)
    review_hosts = []
    for host in selected_hosts:
        fields, notes = _host_fields(host, evidence)
        review_hosts.append({
            "host": _text(host.get("host")), "ip": _text(host.get("ip")), "kind": _text(host.get("kind")),
            "role": _role(host), "environment": _environment(host), "fields": fields, "collection_notes": notes,
        })
    sections = []
    for group in _FIELD_GROUPS:
        fields = [{"key": key, "label": label, "control": control, "value": values.get(key, "")} for key, label, control in group["fields"]]
        sections.append({"id": group["id"], "title": group["title"], "fields": fields})
    responsible_values = {str(k): _text(v) for k, v in dict(responsibles or {}).items()}
    return {
        "schema": "n2-documentation-review-v1",
        "site_id": _text((context.get("site") or {}).get("site_id") or site_id),
        "client": _text((context.get("site") or {}).get("alias") or site_id),
        "date": date.today().strftime("%d/%m/%Y"),
        "responsibles": responsible_values,
        "selected_hosts": review_hosts,
        "sections": sections,
        "collection": {"execution_ids": [_text(item) for item in execution_ids if _text(item)], "completed_execution_ids": completed, "errors": errors, "evidence_count": len(evidence)},
        "security": {"credentials_included": False, "password_fields": "omitted", "rule": "Senhas, communities, tokens e secrets não são coletados nem exportados.", "server_reboot": "absolute_denial"},
        "review_instructions": [
            "Todos os campos desta tela podem ser revisados e editados pelo analista antes da exportação.",
            "Campos sem evidência permanecem em branco; não preencher por suposição.",
            "Senhas e demais segredos não possuem campo de edição nem de exportação.",
        ],
    }


def sanitize_n2_review(review: dict[str, Any]) -> dict[str, Any]:
    """Remove chaves sensíveis caso um payload tente inseri-las no export."""
    forbidden = {"password", "senha", "secret", "token", "community", "private_key", "credential"}

    def clean(value: Any, key: str = "") -> Any:
        if any(word in key.casefold() for word in forbidden):
            return None
        if isinstance(value, dict):
            return {str(k): clean(v, str(k)) for k, v in value.items() if not any(word in str(k).casefold() for word in forbidden)}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str) and re.search(r"(?i)\b(password|senha|community|token|secret)\s*[:=]", value):
            return re.sub(r"(?i)\b(password|senha|community|token|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
        return value

    return clean(deepcopy(review))
