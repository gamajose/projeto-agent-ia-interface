from __future__ import annotations

import ipaddress
import re
import shlex
from collections import defaultdict
from typing import Any
from uuid import uuid4

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.redaction import redact_text
from app.services.runner import ResolvedTarget, build_executor
from app.services.runtime_env import runtime_value


PROJECT_SCENARIOS: dict[str, dict[str, str]] = {
    "linux_prod_std": {"label": "Servidor Linux — Produção/Standby", "playbook_id": "project-linux-prod-std"},
    "linux_monitoring": {"label": "Servidor Linux — Monitoramento", "playbook_id": "project-linux-monitoring"},
    "management_interface": {"label": "Interface de gerenciamento", "playbook_id": "project-management-interface"},
    "firewall": {"label": "Firewall", "playbook_id": "project-firewall"},
    "windows": {"label": "Servidor Windows", "playbook_id": "project-windows"},
    "dns_vpn": {"label": "Ajuste de DNS da VPN", "playbook_id": "network-dns-vpn-resolution"},
}

ROLE_LABELS = {
    "production": "Produção",
    "standby": "Standby",
    "monitoring": "Monitoramento",
    "server": "Servidor",
    "unknown": "Servidor",
}

INTERFACE_LABELS = {
    "idrac": "iDRAC (Dell)",
    "ilo": "iLO (HPE)",
    "ilom": "ILOM (Oracle/Sun)",
    "xclarity": "xClarity (Lenovo)",
    "unknown": "Interface a identificar",
    "none": "Sem interface identificada",
}


class ProjectPlanError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ip(value: Any, label: str, *, required: bool = False) -> str:
    raw = _text(value)
    if not raw:
        if required:
            raise ProjectPlanError(f"{label} é obrigatório")
        return ""
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError as exc:
        raise ProjectPlanError(f"{label} deve conter um endereço IP válido") from exc


def _hostname(value: Any, label: str, default: str = "") -> str:
    raw = _text(value) or default
    raw = re.sub(r"^https?://", "", raw, flags=re.I).split("/", 1)[0]
    if not raw or not re.fullmatch(r"[A-Za-z0-9_.:-]+", raw):
        raise ProjectPlanError(f"{label} contém caracteres inválidos")
    return raw


def _ctx(key: str, label: str, target: str = "", kind: str = "remote") -> dict[str, str]:
    return {"key": key, "label": label, "target": target, "kind": kind}


def _step(
    step_id: str,
    title: str,
    context: str,
    kind: str,
    purpose: str,
    command: str = "",
    *,
    automated: bool = False,
    approval_required: bool = False,
    evidence: str = "Registrar a saída no ticket e tirar print quando aplicável.",
    notes: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": step_id,
        "title": title,
        "context": context,
        "kind": kind,
        "purpose": purpose,
        "command": command,
        "automated": automated,
        "approval_required": approval_required,
        "evidence": evidence,
        "notes": list(notes),
        "status": "pending",
    }


def _execution(reference: str, label: str, environment: str, playbook_id: str, objective: str) -> dict[str, Any]:
    return {
        "reference": reference,
        "label": label,
        "environment": environment,
        "playbook_id": playbook_id,
        "objective": objective,
        "ssh_port": 22,
    }


def _macro(lines: list[str]) -> str:
    body = "\n".join(f"⭕ – {line}" for line in lines)
    return (
        "Macro noc_n1 — validação de projeto\n"
        + body
        + "\n\nℹ️ Informação\n▶️ Em andamento\n⬆️ Pendente relacionado\n"
        "⛔ Não se aplica\n⭕ Pendente\n✅ Concluído"
    )


def _infrastructure(settings: Settings) -> dict[str, str]:
    monitor1 = _text(settings.ssh_bastion_host) or _text(runtime_value("SSH_SRV_VPN_IP", "10.17.181.1", settings=settings))
    monitor1_user = (
        _text(settings.ssh_bastion_user)
        or _text(runtime_value("SSH_SRV_VPN_USER", "", settings=settings))
        or _text(settings.ssh_default_user)
        or "2com"
    )
    cmk05 = _text(runtime_value("SSH_CMK05", "", settings=settings)) or _text(runtime_value("SSH_CMK05_IP", "10.17.181.44", settings=settings))
    whatsapp = _text(runtime_value("API_WHATSAPP", "ws.2comconsulting.com.br", settings=settings))
    return {
        "monitor1_ip": _ip(monitor1, "SSH_SRV_VPN_IP", required=True),
        "monitor1_user": monitor1_user,
        "cmk05_ip": _ip(cmk05, "SSH_CMK05", required=True),
        "whatsapp_host": _hostname(whatsapp, "API_WHATSAPP", "ws.2comconsulting.com.br"),
    }


def _environment(scenario: str, role: str) -> EnvironmentType:
    if scenario == "linux_monitoring":
        return EnvironmentType.MONITORING
    if role == "standby":
        return EnvironmentType.STANDBY
    if role == "monitoring":
        return EnvironmentType.MONITORING
    if scenario == "windows":
        return EnvironmentType.PRODUCTION
    return EnvironmentType.PRODUCTION


def _target_label(scenario: str, role: str) -> str:
    if scenario == "linux_prod_std":
        return ROLE_LABELS.get(role, "Produção")
    if scenario == "linux_monitoring":
        return "Servidor de monitoramento"
    if scenario == "management_interface":
        return "Servidor físico"
    if scenario == "firewall":
        return "Firewall"
    if scenario == "windows":
        return "Servidor Windows"
    if scenario == "dns_vpn":
        return "Servidor da VPN"
    return "Servidor"


def _run_collect(executor: Any, environment: EnvironmentType, command: str, *, sudo: bool = False, timeout: int = 45) -> dict[str, Any]:
    try:
        result = executor.run_sudo(command, environment, timeout=timeout) if sudo else executor.run(command, environment, timeout=timeout)
        return {
            "exit_code": int(result.exit_code),
            "stdout": redact_text(str(result.stdout or ""))[-8000:],
            "stderr": redact_text(str(result.stderr or ""))[-3000:],
        }
    except Exception as exc:
        return {"exit_code": 255, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def _release_value(output: str, key: str) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*[\"']?([^\"'\n]+)", output or "")
    return _text(match.group(1)) if match else ""


def _detect_os(release: str, fallback: str = "") -> tuple[str, str]:
    combined = f"{release}\n{fallback}".casefold()
    pretty = _release_value(release, "PRETTY_NAME") or _release_value(release, "NAME")
    version = _release_value(release, "VERSION_ID")
    if "oracle linux" in combined or "ol" == _release_value(release, "ID").casefold():
        major = (version.split(".", 1)[0] if version else "")
        family = f"oracle{major}" if major in {"7", "8", "9"} else "oracle"
        return family, pretty or f"Oracle Linux {version}".strip()
    if "ubuntu" in combined:
        return "ubuntu", pretty or "Ubuntu"
    if "debian" in combined:
        return "debian", pretty or "Debian"
    if any(token in combined for token in ("red hat", "rocky", "almalinux", "centos")):
        return "rhel", pretty or "RHEL compatível"
    if "pfsense" in combined or "freebsd" in combined:
        return "pfsense", pretty or "pfSense / FreeBSD"
    if any(token in combined for token in ("fortigate", "fortios", "fortinet")):
        return "fortigate", pretty or "FortiGate / FortiOS"
    return "unknown", pretty or "Não identificado"


def _detect_internal_ip(network: str, vpn_ip: str) -> str:
    source_match = re.search(r"(?m)^.*\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})\b", network or "")
    if source_match:
        candidate = source_match.group(1)
        if candidate != vpn_ip and not candidate.startswith("127."):
            return candidate

    addresses: list[str] = []
    for match in re.finditer(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})/\d+", network or ""):
        candidate = match.group(1)
        if candidate.startswith("127.") or candidate == vpn_ip:
            continue
        if candidate not in addresses:
            addresses.append(candidate)
    private = [item for item in addresses if ipaddress.ip_address(item).is_private]
    return (private or addresses or [""])[0]


def _detect_hardware(hardware: str) -> tuple[str, str]:
    manufacturer_match = re.search(r"(?im)^\s*Manufacturer:\s*(.+)$", hardware or "")
    product_match = re.search(r"(?im)^\s*Product Name:\s*(.+)$", hardware or "")
    manufacturer = _text(manufacturer_match.group(1)) if manufacturer_match else ""
    product = _text(product_match.group(1)) if product_match else ""
    return manufacturer, product


def _detect_management_type(manufacturer: str, product: str, bmc: str) -> str:
    text = f"{manufacturer} {product} {bmc}".casefold()
    if any(token in text for token in ("dell", "poweredge", "idrac")):
        return "idrac"
    if any(token in text for token in ("hewlett", "hpe", "proliant", " ilo ")):
        return "ilo"
    if any(token in text for token in ("oracle", "sun microsystems", "ilom")):
        return "ilom"
    if any(token in text for token in ("lenovo", "system x", "xclarity", "ibm")):
        return "xclarity"
    return "unknown"


def _detect_management_ip(bmc: str) -> str:
    match = re.search(r"(?im)^\s*IP Address\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", bmc or "")
    if not match:
        return ""
    candidate = match.group(1)
    return "" if candidate in {"0.0.0.0", "255.255.255.255"} else candidate


def _discover_host(vpn_ip: str, environment: EnvironmentType, settings: Settings) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "vpn_ip": vpn_ip,
        "reachable": False,
        "os_family": "unknown",
        "os_name": "Não identificado",
        "internal_ip": "",
        "virtualization": "unknown",
        "machine_type": "desconhecida",
        "manufacturer": "",
        "model": "",
        "management_type": "unknown",
        "management_ip": "",
        "agent_6556": "unknown",
        "time_sync": "unknown",
        "error": "",
    }
    executor = None
    try:
        target = ResolvedTarget(
            reference=vpn_ip,
            host=vpn_ip,
            port=int(settings.ssh_default_port),
            environment=environment,
            inventory=None,
        )
        executor = build_executor(target, settings=settings)
        executor.connect()
        facts["reachable"] = True

        release = _run_collect(executor, environment, "cat /etc/os-release 2>/dev/null || cat /etc/*release 2>/dev/null | head -n 80")
        virtualization = _run_collect(executor, environment, "systemd-detect-virt 2>/dev/null || true")
        network = _run_collect(
            executor,
            environment,
            "ip -o -4 addr show scope global 2>/dev/null; echo __DEFAULT_SOURCE__; ip route get 1.1.1.1 2>/dev/null | head -n 1; echo __ROUTES__; ip route show 2>/dev/null",
        )
        hardware = _run_collect(executor, environment, "dmidecode -t1 2>/dev/null", sudo=True)
        bmc = _run_collect(executor, environment, "command -v ipmitool >/dev/null 2>&1 && ipmitool lan print 2>/dev/null || true", sudo=True)
        timedate = _run_collect(executor, environment, "timedatectl 2>/dev/null || date")
        agent = _run_collect(
            executor,
            environment,
            "ss -lntp 2>/dev/null | grep -E '(:|\\])6556[[:space:]]' || true; systemctl is-active check-mk-agent.socket check_mk.socket xinetd 2>/dev/null || true",
            sudo=True,
        )

        release_text = release["stdout"]
        family, os_name = _detect_os(release_text, hardware["stdout"])
        manufacturer, model = _detect_hardware(hardware["stdout"])
        virt = _text(virtualization["stdout"].splitlines()[0] if virtualization["stdout"].splitlines() else "")
        facts.update(
            {
                "os_family": family,
                "os_name": os_name,
                "internal_ip": _detect_internal_ip(network["stdout"], vpn_ip),
                "virtualization": virt or "unknown",
                "machine_type": "física" if virt == "none" else ("virtual" if virt and virt != "unknown" else "desconhecida"),
                "manufacturer": manufacturer,
                "model": model,
                "management_type": _detect_management_type(manufacturer, model, bmc["stdout"]),
                "management_ip": _detect_management_ip(bmc["stdout"]),
                "agent_6556": "listening" if re.search(r"6556", agent["stdout"]) else "not_confirmed",
                "time_sync": "synchronized" if re.search(r"(?i)(System clock synchronized:\s*yes|synchronized:\s*yes)", timedate["stdout"]) else "not_confirmed",
            }
        )
    except Exception as exc:
        facts["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if executor is not None:
            try:
                executor.close()
            except Exception:
                pass
    return facts


def _related_hosts(values: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, item in enumerate(values):
        role = _text(item.get("role")) or "server"
        vpn_ip = _ip(item.get("vpn_ip"), f"IP VPN do host relacionado {index + 1}", required=True)
        rows.append({"role": role, "vpn_ip": vpn_ip})
    return rows


def discover_project_context(payload: dict[str, Any], *, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    scenario = _text(payload.get("scenario"))
    role = _text(payload.get("role")) or ("monitoring" if scenario == "linux_monitoring" else "production")
    target_vpn = _ip(payload.get("target_vpn_ip"), "IP VPN/TAP do alvo", required=True)
    has_monitor = bool(payload.get("has_monitoring_server"))
    monitor_vpn = _ip(payload.get("monitoring_vpn_ip"), "IP VPN do servidor de monitoramento")
    related = _related_hosts(list(payload.get("related_hosts") or []))

    result: dict[str, Any] = {
        "source": "automatic_ssh",
        "target": {},
        "monitoring_server": None,
        "related_hosts": [],
    }

    # O fluxo Windows permanece orientado a RDP/Socat. Para todos os demais
    # cenários Linux/appliance, o primeiro passo é descobrir os dados no alvo.
    if scenario != "windows":
        result["target"] = _discover_host(target_vpn, _environment(scenario, role), settings)
    else:
        result["target"] = {
            "vpn_ip": target_vpn,
            "reachable": None,
            "os_family": "windows",
            "os_name": "Windows — identificar por systeminfo",
            "internal_ip": "",
            "virtualization": "unknown",
            "machine_type": "desconhecida",
            "manufacturer": "",
            "model": "",
            "management_type": "unknown",
            "management_ip": "",
            "agent_6556": "unknown",
            "time_sync": "unknown",
            "error": "",
        }

    if has_monitor and monitor_vpn:
        result["monitoring_server"] = _discover_host(monitor_vpn, EnvironmentType.MONITORING, settings)

    if scenario == "linux_monitoring":
        for item in related:
            related_environment = EnvironmentType.STANDBY if item["role"] == "standby" else EnvironmentType.PRODUCTION
            facts = _discover_host(item["vpn_ip"], related_environment, settings)
            result["related_hosts"].append({**item, **facts})

    return result


def _linux_package_command(os_family: str) -> str:
    if os_family in {"ubuntu", "debian"}:
        return "apt-get update && apt-get install -y vim ipmitool snmp snmpd socat chrony htop netcat-openbsd traceroute"
    return "yum install -y vim ipmitool net-snmp-utils net-snmp socat chrony htop nc traceroute"


def _linux_common(os_family: str, facts: dict[str, Any], *, packages: bool = True) -> list[dict[str, Any]]:
    rows = [
        _step(
            "root-access",
            "Validar acesso VPN com usuários 2com/root",
            "target",
            "manual",
            "A IA acessa o IP VPN informado; após a sessão, confirmar elevação para root sem expor senha.",
            evidence="Tirar print da sessão já como root, sem exibir a senha.",
        ),
        _step(
            "virtualization",
            "Identificar se a máquina é física ou virtual",
            "target",
            "command",
            "O tipo é descoberto automaticamente no servidor; não precisa ser informado na tela.",
            "systemd-detect-virt",
            automated=True,
            evidence=f"Pré-validação: {facts.get('machine_type') or 'não identificado'} ({facts.get('virtualization') or 'sem retorno'}).",
        ),
        _step(
            "hardware",
            "Coletar informações do equipamento",
            "target",
            "command",
            "Descobrir fabricante e modelo diretamente no host.",
            "dmidecode -t1",
            automated=True,
            evidence=f"Pré-validação: {facts.get('manufacturer') or 'fabricante não identificado'} {facts.get('model') or ''}".strip(),
        ),
        _step(
            "os-version",
            "Validar versão do sistema operacional",
            "target",
            "command",
            "A versão do SO é descoberta automaticamente; o operador não precisa conhecê-la antes.",
            "cat /etc/*-release",
            automated=True,
            evidence=f"Pré-validação: {facts.get('os_name') or 'não identificado'}.",
        ),
        _step(
            "local-ip",
            "Descobrir e registrar IP interno do servidor",
            "target",
            "command",
            "A IA identifica o endereço interno a partir das interfaces e da rota do host.",
            "ip a",
            automated=True,
            evidence=f"IP interno identificado: {facts.get('internal_ip') or 'a confirmar durante a investigação'}.",
        ),
        _step(
            "time-sync",
            "Validar data e hora sincronizadas",
            "target",
            "command",
            "Confirmar timezone, NTP e estado de sincronização.",
            "timedatectl",
            automated=True,
            evidence=f"Pré-validação de horário: {facts.get('time_sync') or 'não confirmada'}.",
        ),
    ]
    if packages:
        command = _linux_package_command(os_family) if os_family != "unknown" else "# A IA identifica o SO antes de escolher yum/dnf/apt e instalar os utilitários necessários."
        rows.insert(
            4,
            _step(
                "n1-packages",
                "Instalar pacotes utilizados nas validações N1",
                "target",
                "change",
                "A instalação só é preparada depois da identificação automática do SO.",
                command,
                approval_required=True,
                evidence="Registrar o resultado. Não executar automaticamente em produção/standby.",
            ),
        )
    return rows


def _agent_steps(os_family: str, target_vpn_ip: str, monitor1_user: str) -> list[dict[str, Any]]:
    if os_family in {"ubuntu", "debian"}:
        package = "check-mk-agent_2.0.0p25-1_all.deb"
        install = f"cd /tmp && apt install -y ./{package}"
    elif os_family == "unknown":
        package = "PACOTE_CHECKMK_COMPATIVEL_COM_SO_DESCOBERTO"
        install = "# Instalar o pacote Checkmk compatível somente após a IA confirmar o sistema operacional."
    else:
        package = "check-mk-agent-2.0.0p25-1.noarch.rpm"
        install = f"cd /tmp && yum install -y {package}"
    return [
        _step(
            "agent-copy",
            "Copiar o agente Checkmk pelo Monitor 1",
            "monitor1",
            "change",
            "Usar o Monitor 1 configurado no .env para disponibilizar o pacote no /tmp do servidor.",
            f"scp /home/{monitor1_user}/{package} 2com@{target_vpn_ip}:/tmp/",
            approval_required=True,
            evidence="Registrar a transferência sem expor credenciais.",
        ),
        _step(
            "agent-install",
            "Instalar o agente Checkmk",
            "target",
            "change",
            "Instalar o agente adequado ao SO que a IA identificou.",
            install,
            approval_required=True,
            evidence="Tirar print do resultado e registrar o pacote instalado.",
        ),
        _step(
            "agent-local-validation",
            "Validar listener e saída local do agente",
            "target",
            "command",
            "Confirmar pacote, socket/xinetd, porta 6556 e resposta local.",
            "rpm -qa 2>/dev/null | grep -i check-mk || dpkg -l 2>/dev/null | grep -i check-mk || true; systemctl status check_mk.socket check-mk-agent.socket xinetd --no-pager 2>/dev/null; ss -lntp | grep 6556 || true; check_mk_agent | head -n 20",
            automated=True,
        ),
    ]


def _management_steps(facts: dict[str, Any], probe_context: str) -> list[dict[str, Any]]:
    interface_type = _text(facts.get("management_type")) or "unknown"
    interface_ip = _text(facts.get("management_ip"))
    rows = [
        _step(
            "management-detect",
            "Descobrir interface de gerenciamento",
            "target",
            "command",
            "A IA executa ipmitool e correlaciona fabricante/modelo para identificar iDRAC, iLO, ILOM ou xClarity.",
            "ipmitool lan print",
            automated=True,
            evidence=(
                f"Pré-validação: {INTERFACE_LABELS.get(interface_type, interface_type)}"
                + (f" em {interface_ip}." if interface_ip else "; IP ainda não confirmado.")
            ),
            notes=("iDRAC → Dell", "iLO → HPE", "xClarity → Lenovo", "ILOM → Oracle/Sun"),
        )
    ]
    if not interface_ip:
        rows.append(
            _step(
                "management-classification",
                "Confirmar se existe interface de gerenciamento",
                "target",
                "manual",
                "Se o ipmitool não retornar endereço, a IA registra a evidência e a macro fica pendente/não se aplica conforme o resultado.",
                evidence="Não é necessário informar o tipo ou o IP previamente na interface.",
            )
        )
        return rows

    if interface_type == "ilom":
        command = (
            "snmpwalk -v3 -l authNoPriv -u ${SNMP_V3_USER} -a SHA "
            f"-A ${{SNMP_V3_AUTH_PASSWORD}} {interface_ip} | head -n 10"
        )
    else:
        command = f"snmpwalk -v2c -c ${{SNMP_V2_COMMUNITY}} {interface_ip} | head -n 10"
    rows.append(
        _step(
            "management-snmp",
            f"Validar SNMP da {INTERFACE_LABELS.get(interface_type, interface_type)}",
            probe_context,
            "command",
            "Confirmar resposta SNMP usando as credenciais mantidas no .env/Vault.",
            command,
            evidence="Tirar print das primeiras respostas. Se falhar no host, repetir pelo monitor compartilhado informado somente pelo IP VPN.",
        )
    )
    return rows


def _discovery_warnings(discovery: dict[str, Any], scenario: str) -> list[str]:
    warnings: list[str] = []
    target = discovery.get("target") or {}
    if scenario != "windows" and not target.get("reachable"):
        warnings.append(f"Não foi possível concluir a pré-descoberta por SSH no IP VPN informado: {target.get('error') or 'sem detalhe' }.")
        return warnings
    if scenario != "windows" and target.get("os_family") == "unknown":
        warnings.append("O SO ainda não foi identificado; a investigação da IA deve confirmar a distribuição antes de qualquer instalação.")
    if scenario in {"linux_prod_std", "linux_monitoring", "management_interface"} and not target.get("internal_ip"):
        warnings.append("O IP interno ainda não foi identificado automaticamente; a IA continuará a descoberta pelo próprio host.")
    return warnings


def _fact_summary(facts: dict[str, Any]) -> str:
    if not facts:
        return "sem pré-descoberta"
    parts = [facts.get("os_name") or "SO não identificado"]
    if facts.get("internal_ip"):
        parts.append(f"IP interno {facts['internal_ip']}")
    if facts.get("machine_type"):
        parts.append(f"máquina {facts['machine_type']}")
    if facts.get("model"):
        parts.append(str(facts["model"]))
    if facts.get("management_ip"):
        parts.append(f"{INTERFACE_LABELS.get(str(facts.get('management_type')), 'BMC')} {facts['management_ip']}")
    return "; ".join(str(item) for item in parts if item)


def build_project_plan(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    discovery: dict[str, Any] | None = None,
    perform_discovery: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()
    scenario = _text(payload.get("scenario"))
    if scenario not in PROJECT_SCENARIOS:
        raise ProjectPlanError("cenário de projeto inválido")

    role = _text(payload.get("role")) or ("monitoring" if scenario == "linux_monitoring" else "production")
    if scenario == "linux_prod_std" and role not in {"production", "standby"}:
        raise ProjectPlanError("produção/standby exige selecionar o papel do servidor")

    target_vpn = _ip(payload.get("target_vpn_ip"), "IP VPN/TAP do alvo", required=True)
    install_agent = bool(payload.get("install_agent", True))
    has_monitor = bool(payload.get("has_monitoring_server"))
    monitor_vpn = _ip(payload.get("monitoring_vpn_ip"), "IP VPN do servidor de monitoramento")
    if has_monitor and not monitor_vpn:
        raise ProjectPlanError("informe somente o IP VPN/TAP do servidor de monitoramento")
    related = _related_hosts(list(payload.get("related_hosts") or []))
    gateway_dns = _ip(payload.get("gateway_dns"), "DNS do gateway")
    vpn_dns = _hostname(payload.get("vpn_dns_name"), "nome DNS da VPN", "vpn.oracledba.com.br")
    infra = _infrastructure(settings)

    if discovery is None:
        discovery = discover_project_context(payload, settings=settings) if perform_discovery else {
            "source": "deferred",
            "target": {"vpn_ip": target_vpn, "os_family": "unknown", "os_name": "A descobrir", "internal_ip": "", "management_type": "unknown", "management_ip": ""},
            "monitoring_server": None,
            "related_hosts": [],
        }

    target_facts = dict(discovery.get("target") or {})
    monitor_facts = dict(discovery.get("monitoring_server") or {}) if discovery.get("monitoring_server") else {}
    related_facts = list(discovery.get("related_hosts") or [])
    os_family = _text(target_facts.get("os_family")) or "unknown"
    target_internal = _text(target_facts.get("internal_ip"))
    monitor_internal = _text(monitor_facts.get("internal_ip"))
    target_label = _target_label(scenario, role)
    playbook_id = PROJECT_SCENARIOS[scenario]["playbook_id"]
    scenario_label = PROJECT_SCENARIOS[scenario]["label"]

    contexts = [
        _ctx("target", target_label, target_vpn),
        _ctx("monitor1", "Monitor 1 (.env)", infra["monitor1_ip"]),
        _ctx("manual", "Validação manual", kind="manual"),
    ]
    if has_monitor:
        contexts.append(_ctx("client_monitor", "Servidor de monitoramento do cliente", monitor_vpn))
    if scenario == "linux_monitoring":
        contexts.append(_ctx("cmk05", "Monitor 5 / CMK05 (.env)", infra["cmk05_ip"]))

    steps: list[dict[str, Any]] = [
        _step("ind-panel", "Validar painel de indisponibilidade e notificações", "manual", "manual", "Confirmar inclusão do host e notificações.", evidence="Tirar print do host no painel."),
        _step("whatsapp-bots", "Validar bots de notificação no grupo", "manual", "manual", "Confirmar presença dos bots exigidos.", evidence="Tirar print dos bots no grupo."),
    ]
    warnings = _discovery_warnings(discovery, scenario)
    executions: list[dict[str, Any]] = []
    macro: list[str] = []

    if scenario in {"linux_prod_std", "linux_monitoring"}:
        steps += _linux_common(os_family, target_facts)
        probe_context = "client_monitor" if has_monitor and scenario != "linux_monitoring" else "target"
        steps += _management_steps(target_facts, probe_context)
        if install_agent:
            steps += _agent_steps(os_family, target_vpn, infra["monitor1_user"])
        steps += [
            _step("target-to-monitor1-6556", "Validar 6556 do host para o Monitor 1", "target", "command", "Comprovar comunicação de saída usando o Monitor 1 carregado do .env.", f"nc -v -w5 {infra['monitor1_ip']} 6556 | head", automated=True),
            _step("monitor1-to-target-6556", "Validar 6556 do Monitor 1 para o host", "monitor1", "command", "Comprovar o processo inverso pelo IP VPN/TAP informado.", f"nc -v -w5 {target_vpn} 6556 | head"),
        ]

        if scenario == "linux_monitoring":
            steps += [
                _step("monitor1-ping", "Validar ping com o Monitor 1", "target", "command", "Confirmar comunicação básica.", f"ping -c 4 {infra['monitor1_ip']}", automated=True),
                _step("livestatus-label", "Validar label cmk/check_mk_server:yes", "manual", "manual", "Confirmar label de Livestatus no Checkmk.", evidence="Tirar print da configuração."),
                _step("livestatus-rule", "Validar regra de Livestatus no painel", "manual", "manual", "Confirmar regra específica no painel.", evidence="Tirar print ou marcar não se aplica."),
                _step("cmk05-listener", "Abrir listener 6557 no Monitor 5", "cmk05", "listener", "Usar o CMK05 definido em SSH_CMK05. O usuário/senha seguem a mesma credencial operacional do Monitor 1.", "nc -l 6557", evidence="Manter o terminal aberto durante o teste."),
                _step("target-to-cmk05", "Validar comunicação com o Monitor 5 pela 6557", "target", "command", "Confirmar conexão enquanto o listener estiver aberto.", f"nc -v -w5 {infra['cmk05_ip']} 6557 | head", automated=True),
                _step("whatsapp-api", "Validar API do WhatsApp pela porta 443", "target", "command", "O hostname é carregado de API_WHATSAPP no .env.", f"nc -v -w3 {infra['whatsapp_host']} 443 | head", automated=True),
            ]

            facts_by_vpn = {str(item.get("vpn_ip")): item for item in related_facts}
            for index, host in enumerate(related):
                facts = dict(facts_by_vpn.get(host["vpn_ip"]) or {})
                internal = _text(facts.get("internal_ip"))
                label = ROLE_LABELS.get(host["role"], f"Host {index + 1}")
                key = f"related_{index}"
                contexts.append(_ctx(key, label, host["vpn_ip"]))
                if internal:
                    steps.append(_step(f"monitor-to-related-{index}", f"Validar 6556 do monitor para {label}", "target", "command", "Usar o IP interno descoberto automaticamente no host relacionado.", f"nc -v -w5 {internal} 6556 | head", automated=True, evidence=f"IP interno de {label}: {internal}."))
                else:
                    warnings.append(f"Não foi possível descobrir o IP interno de {label} ({host['vpn_ip']}) antes de montar o teste monitor → host.")
                if target_internal:
                    steps.append(_step(f"related-to-monitor-{index}", f"Validar 6556 de {label} para o monitor", key, "command", "Executar o processo inverso com o IP interno que a IA descobriu no monitor.", f"nc -v -w5 {target_internal} 6556 | head", automated=True))
                else:
                    warnings.append("O IP interno do monitor ainda não foi descoberto; o teste inverso será completado pela investigação da IA.")
                executions.append(_execution(
                    host["vpn_ip"],
                    label,
                    host["role"] if host["role"] in {"production", "standby", "monitoring"} else "unknown",
                    "project-linux-prod-std",
                    f"Validação de projeto. Acesse este host pelo IP VPN {host['vpn_ip']}; descubra SO e IP interno diretamente no servidor. Valide a porta 6556 para o monitor interno {target_internal or 'que deve ser descoberto pela investigação'} e não altere o ambiente.",
                ))
        elif has_monitor:
            if target_internal and monitor_internal:
                steps += [
                    _step("monitor-to-target-internal", "Validar 6556 do monitor para o host", "client_monitor", "command", "Usar os IPs internos descobertos automaticamente.", f"nc -v -w5 {target_internal} 6556 | head", automated=True),
                    _step("target-to-monitor-internal", "Validar 6556 do host para o monitor", "target", "command", "Executar o processo inverso pelo IP interno descoberto no monitor.", f"nc -v -w5 {monitor_internal} 6556 | head", automated=True),
                ]
            else:
                warnings.append("A IA não concluiu um dos IPs internos no preflight; os testes internos serão montados após a descoberta durante a investigação.")
            executions.append(_execution(
                monitor_vpn,
                "Servidor de monitoramento do cliente",
                "monitoring",
                playbook_id,
                f"Validação de projeto. Descubra o IP interno deste monitor pelo próprio host e valide comunicação com o servidor {target_vpn}. O IP interno do alvo pré-descoberto é {target_internal or 'a descobrir'}. Somente leitura.",
            ))

        env = "monitoring" if scenario == "linux_monitoring" else role
        objective = (
            f"Validação de projeto {scenario_label}. O operador informou somente o IP VPN/TAP {target_vpn}. "
            "Descubra automaticamente sistema operacional, IP interno, físico/virtual, fabricante/modelo e interface de gerenciamento; "
            f"valide agente Checkmk e conectividade 6556. Pré-descoberta: {_fact_summary(target_facts)}. "
            "Não instalar pacotes, não reiniciar serviços e não alterar rede durante esta investigação."
        )
        executions.insert(0, _execution(target_vpn, target_label, env, playbook_id, objective))
        macro = [
            f"Classificação física/virtual validada: {target_facts.get('machine_type') or 'pendente'}.",
            "Painel de indisponibilidade e notificações validados.",
            "Acesso VPN 2com/root validado.",
            "Comunicação 6556 validada nos dois sentidos.",
            f"Hardware validado: {(target_facts.get('model') or 'pendente')}.",
            f"Interface de gerenciamento validada: {INTERFACE_LABELS.get(str(target_facts.get('management_type') or 'unknown'))}{(' ' + str(target_facts.get('management_ip'))) if target_facts.get('management_ip') else ''}.",
            f"Versão do SO validada: {target_facts.get('os_name') or 'pendente'}.",
            f"IP interno validado: {target_internal or 'pendente'}.",
            "Data e hora validadas.",
            "Bots de notificação validados.",
        ]
        if scenario == "linux_monitoring":
            macro += ["Livestatus validado.", "Monitor 1 validado por ping.", "Monitor 5 validado pela 6557.", "API do WhatsApp validada pela 443."]

    elif scenario == "management_interface":
        steps += _linux_common(os_family, target_facts, packages=False)
        steps += _management_steps(target_facts, "client_monitor" if has_monitor else "target")
        executions.append(_execution(
            target_vpn,
            target_label,
            "production",
            playbook_id,
            f"Validação de interface de gerenciamento. Acesse {target_vpn} e descubra automaticamente fabricante/modelo, físico/virtual, SO e BMC por dmidecode/ipmitool. Pré-descoberta: {_fact_summary(target_facts)}. Somente leitura.",
        ))
        if has_monitor:
            executions.append(_execution(
                monitor_vpn,
                "Servidor de monitoramento do cliente",
                "monitoring",
                playbook_id,
                f"Usar este servidor de monitoramento para validar via SNMP a interface descoberta no servidor {target_vpn}. Interface pré-descoberta: {target_facts.get('management_ip') or 'a confirmar'}. Descubra o IP interno do monitor automaticamente. Somente leitura.",
            ))
        macro = [
            f"Informações do equipamento físico validadas: {target_facts.get('model') or 'pendente'}.",
            f"Interface de gerenciamento mapeada: {INTERFACE_LABELS.get(str(target_facts.get('management_type') or 'unknown'))}{(' ' + str(target_facts.get('management_ip'))) if target_facts.get('management_ip') else ''}.",
            f"Versão do SO validada: {target_facts.get('os_name') or 'pendente'}.",
        ]

    elif scenario == "firewall":
        firewall_family = _text(target_facts.get("os_family")) or "unknown"
        steps += [
            _step("firewall-identify", "Identificar fabricante do firewall", "target", "command", "A IA deve identificar pfSense/FortiGate/FortiNet pelo próprio equipamento; o operador não informa o fabricante.", "uname -a; cat /etc/version 2>/dev/null || cat /etc/*release 2>/dev/null || true", automated=True, evidence=f"Pré-validação: {target_facts.get('os_name') or firewall_family}."),
            _step("firewall-panel", "Validar firewall no painel", "manual", "manual", "Localizar o host e registrar evidência."),
            _step("firewall-shell", "Validar acesso ao shell do firewall", "target", "manual", "Entrar pela VPN e registrar a sessão."),
            _step("firewall-agent", "Verificar agente Checkmk", "target", "command", "Confirmar pacote e listener.", "pkg info 2>/dev/null | grep -i check || rpm -qa 2>/dev/null | grep -i check || true; sockstat -l 2>/dev/null | grep 6556 || ss -lntp 2>/dev/null | grep 6556 || true", automated=True),
            _step("firewall-to-monitor", "Validar 6556 do firewall para Monitor 1", "target", "command", "Confirmar saída usando SSH_SRV_VPN_IP do .env.", f"nc -v -w5 {infra['monitor1_ip']} 6556 | head", automated=True),
            _step("monitor-to-firewall", "Validar 6556 do Monitor 1 para firewall", "monitor1", "command", "Confirmar processo inverso.", f"nc -v -w5 {target_vpn} 6556 | head"),
        ]
        executions.append(_execution(target_vpn, target_label, "production", playbook_id, f"Validação de projeto de firewall. O operador informou somente {target_vpn}. Identifique automaticamente fabricante/versão, agente e 6556. Não alterar configuração. Pré-descoberta: {_fact_summary(target_facts)}."))
        macro = ["Fabricante do firewall identificado.", "Host no painel validado.", "Shell e versão validados.", "Agente e 6556 validados."]

    elif scenario == "windows":
        if not has_monitor:
            warnings.append("O fluxo Windows pode exigir o IP VPN do servidor de monitoramento para a ponte Socat/RDP.")
        context = "client_monitor" if has_monitor else "manual"
        steps += [
            _step("socat-process", "Verificar Socat em execução", context, "command", "Evitar túnel duplicado.", "ps -ef | grep '[s]ocat'", automated=bool(monitor_vpn)),
            _step("socat-history", "Consultar histórico do Socat", context, "command", "Reutilizar comando anterior válido.", "history | grep socat | tail -n 20"),
            _step("socat-create", "Criar túnel RDP com Socat se necessário", context, "change", "O IP interno do Windows é obtido durante o acesso; não é solicitado na tela.", "socat TCP4-LISTEN:3389,fork,reuseaddr TCP4:IP_INTERNO_DESCOBERTO_DO_WINDOWS:3389 &", approval_required=True),
            _step("windows-rdp", "Acessar Windows por RDP com Vault", "manual", "manual", "Usar MSTSC /admin ou a ponte Socat.", f"mstsc /admin -v {monitor_vpn or target_vpn}:3389", evidence="Tirar print sem expor credenciais."),
            _step("windows-systeminfo", "Identificar físico/virtual, hardware e SO", "manual", "command", "Executar no Windows; esses dados não são solicitados previamente.", 'systeminfo\nsysteminfo | findstr /B /C:"OS Name" /C:"OS Version"'),
            _step("windows-ip", "Descobrir IP interno do Windows", "manual", "command", "Executar no Windows e usar o endereço descoberto nos testes internos.", "ipconfig"),
            _step("windows-agent", "Instalar ou validar agente Checkmk", "manual", "manual", "Transferir instalador aprovado e confirmar serviço."),
        ]
        if monitor_vpn:
            executions.append(_execution(monitor_vpn, "Servidor de monitoramento do cliente", "monitoring", playbook_id, f"Validação de projeto Windows para o alvo VPN {target_vpn}. Descubra o IP interno necessário ao Socat e à porta 6556 sem criar o túnel automaticamente."))
        macro = ["Acesso Windows pelo Vault validado.", "Físico/virtual, hardware, SO e IP registrados.", "Agente e 6556 validados."]

    else:  # dns_vpn
        resolvers = ["8.8.8.8", "1.1.1.1", "9.9.9.9"] + ([gateway_dns] if gateway_dns and gateway_dns not in {"8.8.8.8", "1.1.1.1", "9.9.9.9"} else [])
        steps += [_step("dns-current", "Coletar DNS atual", "target", "command", "Identificar nameservers e versão do SO automaticamente.", "cat /etc/resolv.conf", automated=True)]
        steps += [_step(f"dns-test-{index}", f"Testar DNS {resolver}", "target", "command", "Comparar resolvers.", f"nslookup {vpn_dns} {resolver}", automated=True) for index, resolver in enumerate(resolvers)]
        steps.append(_step("vpn-log", "Validar erros nos logs da VPN", "target", "command", "Correlacionar falha de DNS.", "tail -n 120 /var/log/openvpn_client.log 2>/dev/null || journalctl -u 'openvpn*' -n 120 --no-pager", automated=True))
        if os_family in {"oracle8", "oracle9", "unknown"}:
            steps.append(_step("dns-change-ol8", "Ajustar DNS em Oracle Linux 8/9", "target", "change", "Aplicar somente se a versão descoberta confirmar OL8/OL9.", "nmtui\n# Após salvar: nmcli networking off && nmcli networking on && systemctl restart openvpn-client@client232", approval_required=True, notes=("Confirmar o nome real da unidade OpenVPN.",)))
        if os_family in {"oracle7", "unknown"}:
            steps.append(_step("dns-change-ol7", "Ajustar DNS em Oracle Linux 7", "target", "change", "Aplicar somente se a versão descoberta confirmar OL7.", "vi /etc/sysconfig/network-scripts/ifcfg-INTERFACE\n# Após salvar: systemctl restart network && systemctl restart openvpn@client232", approval_required=True, notes=("Interface e unidade VPN variam por cenário.",)))
        executions.append(_execution(target_vpn, target_label, "production", playbook_id, f"Investigar DNS da VPN em {target_vpn}. Descubra a versão do SO automaticamente, valide {vpn_dns} por {', '.join(resolvers)} e correlacione com openvpn_client.log. Não alterar DNS, rede ou VPN."))
        macro = ["DNS atual coletado.", "Resolução da VPN testada em múltiplos DNS.", "Logs correlacionados.", f"SO identificado: {target_facts.get('os_name') or 'pendente'}.", "Ajuste planejado conforme a versão descoberta do SO."]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for step in steps:
        grouped[step["context"]].append(step)
    groups = [{**context, "items": grouped[context["key"]]} for context in contexts if grouped.get(context["key"])]
    known = {context["key"] for context in contexts}
    groups += [{**_ctx(key, key.replace("_", " ").title()), "items": rows} for key, rows in grouped.items() if key not in known]

    command_count = sum(bool(step["command"]) for step in steps)
    automated_count = sum(step["kind"] == "command" and step["automated"] for step in steps)
    change_count = sum(step["kind"] == "change" for step in steps)

    return {
        "plan_id": str(uuid4()),
        "project_name": f"Validação — {scenario_label}",
        "scenario": scenario,
        "scenario_label": scenario_label,
        "playbook_id": playbook_id,
        "target": {"name": target_label, "vpn_ip": target_vpn, "internal_ip": target_internal},
        "discovery": discovery,
        "groups": groups,
        "warnings": warnings,
        "execution_targets": executions,
        "ticket_macro": _macro(macro),
        "summary": {
            "total_steps": len(steps),
            "command_steps": command_count,
            "automatic_read_only_steps": automated_count,
            "change_steps": change_count,
            "execution_targets": len(executions),
        },
        "safety": {
            "automatic_scope": "descoberta e validações somente leitura; SO, IP interno e interface de gerenciamento são obtidos pela ferramenta",
            "manual_scope": "instalações, Socat, listeners persistentes, reinícios, ajustes DNS/rede, painel e bots",
            "credentials": "Monitor 1, CMK05, WhatsApp e senhas são lidos do .env/Vault e não são solicitados na interface",
        },
    }


def project_templates() -> dict[str, Any]:
    return {
        "scenarios": [{"value": key, **value} for key, value in PROJECT_SCENARIOS.items()],
        "defaults": {
            "vpn_dns_name": "vpn.oracledba.com.br",
            "infrastructure_from_env": True,
        },
    }
