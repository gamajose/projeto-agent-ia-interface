from __future__ import annotations

import ipaddress
import os
import re
import shlex
from collections import defaultdict
from typing import Any
from uuid import uuid4


PROJECT_SCENARIOS: dict[str, dict[str, str]] = {
    "linux_prod_std": {"label": "Servidor Linux — Produção/Standby", "playbook_id": "project-linux-prod-std"},
    "linux_monitoring": {"label": "Servidor Linux — Monitoramento", "playbook_id": "project-linux-monitoring"},
    "management_interface": {"label": "Interface de gerenciamento", "playbook_id": "project-management-interface"},
    "firewall": {"label": "Firewall", "playbook_id": "project-firewall"},
    "windows": {"label": "Servidor Windows", "playbook_id": "project-windows"},
    "dns_vpn": {"label": "Ajuste de DNS da VPN", "playbook_id": "network-dns-vpn-resolution"},
}
OS_LABELS = {
    "oracle7": "Oracle Linux 7", "oracle8": "Oracle Linux 8", "oracle9": "Oracle Linux 9",
    "rhel": "RHEL / compatível", "ubuntu": "Ubuntu", "debian": "Debian",
    "windows": "Windows", "pfsense": "pfSense", "fortigate": "FortiGate/FortiNet", "unknown": "Não informado",
}
INTERFACE_LABELS = {
    "auto": "Detectar pelo equipamento", "idrac": "iDRAC (Dell)", "ilo": "iLO (HPE)",
    "ilom": "ILOM (Oracle/Sun)", "xclarity": "xClarity (Lenovo)", "none": "Sem interface de gerenciamento",
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


def _hostname(value: Any, label: str, default: str) -> str:
    raw = _text(value) or default
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", raw):
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
        "id": step_id, "title": title, "context": context, "kind": kind, "purpose": purpose,
        "command": command, "automated": automated, "approval_required": approval_required,
        "evidence": evidence, "notes": list(notes), "status": "pending",
    }


def _execution(reference: str, label: str, environment: str, playbook_id: str, objective: str) -> dict[str, Any]:
    return {
        "reference": reference, "label": label, "environment": environment,
        "playbook_id": playbook_id, "objective": objective, "ssh_port": 22,
    }


def _macro(lines: list[str]) -> str:
    body = "\n".join(f"⭕ – {line}" for line in lines)
    return (
        "Macro noc_n1 — validação de projeto\n" + body +
        "\n\nℹ️ Informação\n▶️ Em andamento\n⬆️ Pendente relacionado\n"
        "⛔ Não se aplica\n⭕ Pendente\n✅ Concluído"
    )


def _linux_package_command(os_family: str) -> str:
    if os_family in {"ubuntu", "debian"}:
        return "apt-get update && apt-get install -y vim ipmitool snmp snmpd socat chrony htop netcat-openbsd traceroute"
    return "yum install -y vim ipmitool net-snmp-utils net-snmp socat chrony htop nc traceroute"


def _linux_common(os_family: str, *, packages: bool = True) -> list[dict[str, Any]]:
    rows = [
        _step("root-access", "Validar acesso VPN com usuários 2com/root", "target", "manual",
              "Acessar pelo Monitor 1, elevar com sudo su e confirmar o perfil root.",
              evidence="Tirar print da sessão já como root, sem exibir senha."),
        _step("virtualization", "Identificar se a máquina é física ou virtual", "target", "command",
              "Classificar corretamente o equipamento no ticket.", "systemd-detect-virt", automated=True,
              evidence="Tirar print do resultado; 'none' indica máquina física."),
        _step("hardware", "Coletar informações do equipamento", "target", "command",
              "Registrar fabricante, modelo, número de série e tipo.", "dmidecode -t1", automated=True),
        _step("os-version", "Validar versão do sistema operacional", "target", "command",
              "Preencher a versão real do SO na macro.", "cat /etc/*-release", automated=True),
        _step("local-ip", "Registrar IP local do servidor", "target", "command",
              "Identificar endereços internos e interfaces ativas.", "ip a", automated=True),
        _step("time-sync", "Validar data, hora e sincronismo", "target", "command",
              "Confirmar timezone, NTP e estado de sincronização.", "timedatectl", automated=True),
    ]
    if packages:
        rows.insert(4, _step("n1-packages", "Instalar pacotes utilizados nas validações N1", "target", "change",
                            "Disponibilizar utilitários de hardware, SNMP, rede, horário e diagnóstico.",
                            _linux_package_command(os_family), approval_required=True,
                            evidence="Registrar o resultado. Não executar automaticamente em produção/standby."))
    return rows


def _agent_steps(os_family: str, target_vpn_ip: str, monitor1_user: str) -> list[dict[str, Any]]:
    deb = os_family in {"ubuntu", "debian"}
    package = "check-mk-agent_2.0.0p25-1_all.deb" if deb else "check-mk-agent-2.0.0p25-1.noarch.rpm"
    install = f"cd /tmp && apt install -y ./{package}" if deb else f"cd /tmp && yum install -y {package}"
    return [
        _step("agent-copy", "Copiar o agente Checkmk pelo Monitor 1", "monitor1", "change",
              "Disponibilizar o pacote correto no /tmp do servidor do projeto.",
              f"scp /home/{monitor1_user}/{package} 2com@{target_vpn_ip}:/tmp/", approval_required=True,
              evidence="Registrar a transferência sem expor credenciais."),
        _step("agent-install", "Instalar o agente Checkmk", "target", "change",
              "Instalar o agente adequado à família do SO.", install, approval_required=True,
              evidence="Tirar print do resultado e registrar o pacote instalado."),
        _step("agent-local-validation", "Validar listener e saída local do agente", "target", "command",
              "Confirmar pacote, socket/xinetd, porta 6556 e resposta local.",
              "rpm -qa | grep -i check-mk || dpkg -l | grep -i check-mk; systemctl status check_mk.socket xinetd --no-pager 2>/dev/null; ss -lntp | grep 6556 || true; check_mk_agent | head -n 20",
              automated=True),
    ]


def _management_steps(interface_type: str, interface_ip: str, probe_context: str) -> list[dict[str, Any]]:
    rows = [_step(
        "management-detect", "Mapear interface de gerenciamento", "target", "command",
        "Identificar iDRAC, iLO, ILOM ou xClarity e coletar a configuração LAN do BMC.",
        "ipmitool lan print", automated=True,
        notes=("iDRAC → Dell", "iLO → HPE", "xClarity → Lenovo", "ILOM → Oracle/Sun"),
    )]
    if not interface_ip or interface_type == "none":
        rows.append(_step("management-classification", "Confirmar existência da interface de gerenciamento",
                          "target", "manual", "Classificar a interface após analisar o ipmitool.",
                          evidence="Marcar como concluída ou não se aplica na macro."))
        return rows

    # Nunca grave credenciais SNMP no repositório. Elas vêm do ambiente e, quando
    # ausentes, o checklist mostra placeholders explícitos para preenchimento seguro.
    community = os.getenv("SNMP_V2_COMMUNITY", "COMUNIDADE_SNMP").strip() or "COMUNIDADE_SNMP"
    v3_user = os.getenv("SNMP_V3_USER", "doiscom").strip() or "doiscom"
    v3_password = os.getenv("SNMP_V3_AUTH_PASSWORD", "SENHA_SNMP_V3").strip() or "SENHA_SNMP_V3"
    if interface_type == "ilom":
        command = (
            "snmpwalk -v3 -l authNoPriv "
            f"-u {shlex.quote(v3_user)} -a SHA -A {shlex.quote(v3_password)} {interface_ip} | head -n 10"
        )
    else:
        command = f"snmpwalk -v2c -c {shlex.quote(community)} {interface_ip} | head -n 10"
    rows.append(_step(
        "management-snmp", f"Validar SNMP da interface {INTERFACE_LABELS.get(interface_type, interface_type)}",
        probe_context, "command", "Confirmar resposta pelo protocolo esperado.", command,
        evidence="Tirar print das primeiras respostas. Se falhar no host, repetir pelo monitor compartilhado.",
    ))
    return rows


def _related_hosts(values: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, item in enumerate(values):
        name = _text(item.get("name")) or f"Host {index + 1}"
        rows.append({
            "name": name,
            "role": _text(item.get("role")) or "server",
            "internal_ip": _ip(item.get("internal_ip"), f"IP interno de {name}", required=True),
            "vpn_ip": _ip(item.get("vpn_ip"), f"IP VPN de {name}"),
        })
    return rows


def build_project_plan(payload: dict[str, Any]) -> dict[str, Any]:
    scenario = _text(payload.get("scenario"))
    if scenario not in PROJECT_SCENARIOS:
        raise ProjectPlanError("cenário de projeto inválido")

    project_name = _text(payload.get("project_name")) or "Validação de projeto"
    ticket = _text(payload.get("ticket_number"))
    target_name = _text(payload.get("target_name")) or "Servidor do projeto"
    target_vpn = _ip(payload.get("target_vpn_ip"), "IP VPN do alvo", required=True)
    target_internal = _ip(payload.get("target_internal_ip"), "IP interno do alvo")
    monitor1 = _ip(payload.get("monitor1_ip") or "10.17.181.1", "IP do Monitor 1", required=True)
    cmk05 = _ip(payload.get("cmk05_ip") or "10.17.181.44", "IP do Monitor 5", required=True)
    whatsapp = _hostname(payload.get("whatsapp_host"), "host da API do WhatsApp", "ws.2comconsulting.com.br")
    vpn_dns = _hostname(payload.get("vpn_dns_name"), "nome DNS da VPN", "vpn.oracledba.com.br")
    gateway_dns = _ip(payload.get("gateway_dns"), "DNS do gateway")
    os_family = _text(payload.get("os_family")) or "unknown"
    role = _text(payload.get("role")) or "production"
    monitor1_user = _text(payload.get("monitor1_user")) or "jose.moraes"
    install_agent = bool(payload.get("install_agent", True))
    has_monitor = bool(payload.get("has_monitoring_server"))
    monitor_name = _text(payload.get("monitoring_name")) or "Servidor de monitoramento do cliente"
    monitor_vpn = _ip(payload.get("monitoring_vpn_ip"), "IP VPN do servidor de monitoramento")
    monitor_internal = _ip(payload.get("monitoring_internal_ip"), "IP interno do servidor de monitoramento")
    interface_type = _text(payload.get("management_interface_type")) or "auto"
    interface_ip = _ip(payload.get("management_interface_ip"), "IP da interface de gerenciamento")
    related = _related_hosts(list(payload.get("related_hosts") or []))

    contexts = [_ctx("target", target_name, target_vpn), _ctx("monitor1", "Monitor 1", monitor1), _ctx("manual", "Validação manual", kind="manual")]
    if scenario == "linux_monitoring":
        monitor_name, monitor_vpn, monitor_internal = target_name, target_vpn, target_internal
    elif has_monitor:
        if not monitor_vpn and not monitor_internal:
            raise ProjectPlanError("informe ao menos um IP do servidor de monitoramento")
        contexts.append(_ctx("client_monitor", monitor_name, monitor_vpn or monitor_internal))

    steps = [
        _step("ticket-private", "Aplicar ticket privado/ação interna e macro correta", "manual", "manual",
              "Preservar o procedimento operacional do projeto.", evidence="Confirmar ticket privado e macro noc_n1."),
        _step("ind-panel", "Validar painel de indisponibilidade e notificações", "manual", "manual",
              "Confirmar inclusão do host e notificações.", evidence="Tirar print do host no painel."),
        _step("whatsapp-bots", "Validar bots de notificação no grupo", "manual", "manual",
              "Confirmar presença dos bots exigidos.", evidence="Tirar print dos bots no grupo."),
    ]
    warnings: list[str] = []
    macro: list[str] = []
    executions: list[dict[str, Any]] = []
    playbook_id = PROJECT_SCENARIOS[scenario]["playbook_id"]

    if scenario in {"linux_prod_std", "linux_monitoring"}:
        steps += _linux_common(os_family)
        probe_context = "client_monitor" if has_monitor and scenario != "linux_monitoring" else "target"
        steps += _management_steps(interface_type, interface_ip, probe_context)
        if install_agent:
            steps += _agent_steps(os_family, target_vpn, monitor1_user)
        steps += [
            _step("target-to-monitor1-6556", "Validar 6556 do host para o Monitor 1", "target", "command",
                  "Comprovar comunicação de saída.", f"nc -v -w5 {monitor1} 6556 | head", automated=True),
            _step("monitor1-to-target-6556", "Validar 6556 do Monitor 1 para o host", "monitor1", "command",
                  "Comprovar o processo inverso pelo IP VPN/TAP.", f"nc -v -w5 {target_vpn} 6556 | head"),
        ]
        if scenario == "linux_monitoring":
            steps += [
                _step("monitor1-ping", "Validar ping com o Monitor 1", "target", "command",
                      "Confirmar comunicação básica.", f"ping -c 4 {monitor1}", automated=True),
                _step("livestatus-label", "Validar label cmk/check_mk_server:yes", "manual", "manual",
                      "Confirmar label de Livestatus no Checkmk.", evidence="Tirar print da configuração."),
                _step("livestatus-rule", "Validar regra de Livestatus no painel", "manual", "manual",
                      "Confirmar regra específica no painel.", evidence="Tirar print ou marcar não se aplica."),
                _step("cmk05-listener", "Abrir listener 6557 no Monitor 5", "manual", "listener",
                      "Acessar CMK05, elevar para root e manter a porta aberta.",
                      f"ssh jose.moraes@{cmk05}\nsudo su\nnc -l 6557",
                      evidence="Manter o terminal aberto durante o teste."),
                _step("target-to-cmk05", "Validar comunicação com o Monitor 5 pela 6557", "target", "command",
                      "Confirmar conexão enquanto o listener estiver aberto.", f"nc -v -w5 {cmk05} 6557 | head", automated=True),
                _step("whatsapp-api", "Validar API do WhatsApp pela porta 443", "target", "command",
                      "Confirmar conectividade TCP com a API.", f"nc -v -w3 {whatsapp} 443 | head", automated=True),
            ]
            if not target_internal:
                warnings.append("Informe o IP interno do monitor para montar os testes inversos com os demais hosts.")
            for index, host in enumerate(related):
                key = f"related_{index}"
                contexts.append(_ctx(key, f"{host['name']} ({host['role']})", host["vpn_ip"] or host["internal_ip"]))
                steps.append(_step(f"monitor-to-related-{index}", f"Validar 6556 do monitor para {host['name']}", "target", "command",
                                   "Usar a rede interna do cliente.", f"nc -v -w5 {host['internal_ip']} 6556 | head", automated=True))
                if target_internal:
                    steps.append(_step(f"related-to-monitor-{index}", f"Validar 6556 de {host['name']} para o monitor", key, "command",
                                       "Executar o processo inverso com IP interno, nunca VPN.",
                                       f"nc -v -w5 {target_internal} 6556 | head", automated=bool(host["vpn_ip"])))
                if host["vpn_ip"]:
                    executions.append(_execution(host["vpn_ip"], host["name"], host["role"] if host["role"] in {"production", "standby", "monitoring"} else "unknown",
                                                 playbook_id, f"Projeto {project_name}. Validar comunicação de {host['name']} para o monitor interno {target_internal or 'não informado'}:6556. Somente leitura."))
        elif has_monitor and monitor_internal and target_internal:
            steps += [
                _step("monitor-to-target-internal", "Validar 6556 do monitor para o host", "client_monitor", "command",
                      "Testar pela rede interna.", f"nc -v -w5 {target_internal} 6556 | head", automated=bool(monitor_vpn)),
                _step("target-to-monitor-internal", "Validar 6556 do host para o monitor", "target", "command",
                      "Executar o processo inverso pelo IP interno.", f"nc -v -w5 {monitor_internal} 6556 | head", automated=True),
            ]
            if monitor_vpn:
                executions.append(_execution(monitor_vpn, monitor_name, "monitoring", playbook_id,
                                             f"Projeto {project_name}. Validar {target_internal}:6556 a partir do monitor do cliente. Somente leitura."))
        env = "monitoring" if scenario == "linux_monitoring" else (role if role in {"production", "standby"} else "production")
        executions.insert(0, _execution(target_vpn, target_name, env, playbook_id,
            f"Projeto {project_name}{f' ticket {ticket}' if ticket else ''}. Validar {PROJECT_SCENARIOS[scenario]['label']}: virtualização, hardware, SO, interfaces, horário, agente Checkmk e conectividade. Não instalar, não reiniciar e não alterar rede."))
        macro = ["Classificação física/virtual validada.", "Painel de indisponibilidade e notificações validados.",
                 "Acesso VPN 2com/root validado.", "Comunicação 6556 validada nos dois sentidos.",
                 "Hardware e interface de gerenciamento validados.", "Versão do SO, IP local, data e hora validados.",
                 "Bots de notificação validados."]
        if scenario == "linux_monitoring":
            macro += ["Livestatus validado.", "Monitor 1 validado por ping.", "Monitor 5 validado pela 6557.", "API do WhatsApp validada pela 443."]

    elif scenario == "management_interface":
        steps += _linux_common(os_family, packages=False)[:4]
        steps += _management_steps(interface_type, interface_ip, "client_monitor" if has_monitor else "target")
        execution_ref = monitor_vpn if has_monitor and monitor_vpn else target_vpn
        execution_label = monitor_name if has_monitor and monitor_vpn else target_name
        executions.append(_execution(execution_ref, execution_label, "monitoring" if has_monitor and monitor_vpn else "production", playbook_id,
                                     f"Projeto {project_name}. Validar interface {interface_ip or 'a identificar'} ({INTERFACE_LABELS.get(interface_type, interface_type)}), hardware e SO. Somente leitura."))
        macro = ["Informações do equipamento físico validadas.", "Interface iDRAC/ILOM/xClarity/iLO mapeada.", "Versão do SO validada."]

    elif scenario == "firewall":
        firewall_type = _text(payload.get("firewall_type")) or "unknown"
        steps += [
            _step("firewall-identify", "Identificar fabricante e macro", "manual", "manual", "Diferenciar pfSense, FortiGate ou FortiNet."),
            _step("firewall-panel", "Validar firewall no painel", "manual", "manual", "Localizar o host e registrar evidência."),
            _step("firewall-shell", "Acessar o shell do firewall", "target", "manual", "Entrar pela VPN e registrar a sessão."),
            _step("firewall-version", "Registrar versão do firewall", "target", "command", "Coletar versão sem alterar configuração.",
                  "uname -a; cat /etc/version 2>/dev/null || cat /etc/*release 2>/dev/null || true", automated=True),
            _step("firewall-agent", "Verificar agente Checkmk", "target", "command", "Confirmar pacote e listener.",
                  "pkg info 2>/dev/null | grep -i check || rpm -qa 2>/dev/null | grep -i check || true; sockstat -l 2>/dev/null | grep 6556 || ss -lntp 2>/dev/null | grep 6556 || true", automated=True),
            _step("firewall-to-monitor", "Validar 6556 do firewall para Monitor 1", "target", "command", "Confirmar saída.", f"nc -v -w5 {monitor1} 6556 | head", automated=True),
            _step("monitor-to-firewall", "Validar 6556 do Monitor 1 para firewall", "monitor1", "command", "Confirmar processo inverso.", f"nc -v -w5 {target_vpn} 6556 | head"),
        ]
        executions.append(_execution(target_vpn, target_name, "production", playbook_id,
                                     f"Projeto {project_name}. Validar firewall {firewall_type}, versão, agente e 6556. Somente leitura."))
        macro = ["Fabricante do firewall identificado.", "Host no painel validado.", "Shell e versão validados.", "Agente e 6556 validados."]

    elif scenario == "windows":
        if not has_monitor:
            warnings.append("O fluxo Windows normalmente exige servidor de monitoramento para Socat e teste inverso.")
        context = "client_monitor" if has_monitor else "manual"
        steps += [
            _step("socat-process", "Verificar Socat em execução", context, "command", "Evitar túnel duplicado.", "ps -ef | grep '[s]ocat'", automated=bool(monitor_vpn)),
            _step("socat-history", "Consultar histórico do Socat", context, "command", "Reutilizar comando anterior válido.", "history | grep socat | tail -n 20"),
            _step("socat-create", "Criar túnel RDP com Socat se necessário", context, "change", "Encaminhar RDP interno.",
                  f"socat TCP4-LISTEN:3389,fork,reuseaddr TCP4:{target_internal or 'IP_INTERNO_DO_WINDOWS'}:3389 &", approval_required=True),
            _step("windows-rdp", "Acessar Windows por RDP com Vault", "manual", "manual", "Usar MSTSC /admin ou a ponte Socat.",
                  f"mstsc /admin -v {monitor_vpn or target_vpn}:3389", evidence="Tirar print sem expor credenciais."),
            _step("windows-systeminfo", "Identificar físico/virtual, hardware e SO", "manual", "command", "Executar no Windows.",
                  'systeminfo\nsysteminfo | findstr /B /C:"OS Name" /C:"OS Version"'),
            _step("windows-ip", "Registrar IP interno do Windows", "manual", "command", "Executar no Windows.", "ipconfig"),
            _step("windows-agent", "Instalar ou validar agente Checkmk", "manual", "manual", "Transferir instalador aprovado e confirmar serviço."),
        ]
        if monitor_internal:
            steps.append(_step("windows-to-monitor", "Validar 6556 do Windows para monitor", "manual", "command", "Usar IP interno do monitor.", f"Test-NetConnection {monitor_internal} -Port 6556"))
        if target_internal:
            steps.append(_step("monitor-to-windows", "Validar 6556 do monitor para Windows", context, "command", "Usar IP interno do Windows.", f"nc -v -w5 {target_internal} 6556 | head", automated=bool(monitor_vpn)))
        if monitor_vpn:
            executions.append(_execution(monitor_vpn, monitor_name, "monitoring", playbook_id,
                                         f"Projeto {project_name}. Validar Socat e 6556 com Windows interno {target_internal or 'não informado'}. Não criar túnel automaticamente."))
        macro = ["Acesso Windows pelo Vault validado.", "Físico/virtual, hardware, SO e IP registrados.", "Agente e 6556 validados."]

    else:  # dns_vpn
        resolvers = ["8.8.8.8", "1.1.1.1", "9.9.9.9"] + ([gateway_dns] if gateway_dns and gateway_dns not in {"8.8.8.8", "1.1.1.1", "9.9.9.9"} else [])
        steps += [_step("dns-current", "Coletar DNS atual", "target", "command", "Identificar nameservers.", "cat /etc/resolv.conf", automated=True)]
        steps += [_step(f"dns-test-{index}", f"Testar DNS {resolver}", "target", "command", "Comparar resolvers.", f"nslookup {vpn_dns} {resolver}", automated=True) for index, resolver in enumerate(resolvers)]
        steps += [
            _step("vpn-log", "Validar erros nos logs da VPN", "target", "command", "Correlacionar falha de DNS.",
                  "tail -n 120 /var/log/openvpn_client.log 2>/dev/null || journalctl -u 'openvpn*' -n 120 --no-pager", automated=True),
            _step("dns-change-ol8", "Ajustar DNS em Oracle Linux 8/9", "target", "change", "Adicionar resolvers pelo NetworkManager.",
                  "nmtui\n# Após salvar: nmcli networking off && nmcli networking on && systemctl restart openvpn-client@client232", approval_required=True,
                  notes=("Confirmar o nome real da unidade OpenVPN.",)),
            _step("dns-change-ol7", "Ajustar DNS em Oracle Linux 7", "target", "change", "Editar ifcfg correto em janela aprovada.",
                  "vi /etc/sysconfig/network-scripts/ifcfg-INTERFACE\n# Após salvar: systemctl restart network && systemctl restart openvpn@client232", approval_required=True,
                  notes=("Interface e unidade VPN variam por cenário.",)),
        ]
        executions.append(_execution(target_vpn, target_name, "production", playbook_id,
                                     f"Investigar DNS da VPN no projeto {project_name}. Validar {vpn_dns} por {', '.join(resolvers)} e correlacionar com openvpn_client.log. Não alterar DNS, rede ou VPN."))
        macro = ["DNS atual coletado.", "Resolução da VPN testada em múltiplos DNS.", "Logs correlacionados.", "Ajuste planejado por versão do SO."]

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
        "plan_id": str(uuid4()), "project_name": project_name, "ticket_number": ticket or None,
        "scenario": scenario, "scenario_label": PROJECT_SCENARIOS[scenario]["label"], "playbook_id": playbook_id,
        "os_family": os_family, "os_label": OS_LABELS.get(os_family, os_family),
        "target": {"name": target_name, "vpn_ip": target_vpn, "internal_ip": target_internal or None},
        "summary": {"total_steps": len(steps), "command_steps": command_count,
                    "automatic_read_only_steps": automated_count, "manual_or_change_steps": len(steps) - automated_count,
                    "change_steps": change_count},
        "warnings": warnings, "groups": groups, "ticket_macro": _macro(macro), "execution_targets": executions,
        "safety": {
            "automatic_scope": "Somente coleta e validações de leitura.",
            "manual_scope": "Instalações, DNS, Socat, listeners e reinícios permanecem manuais ou sujeitos a aprovação.",
            "evidence": "Cada etapa informa onde executar e qual evidência registrar.",
        },
    }


def project_templates() -> dict[str, Any]:
    return {
        "scenarios": [{"value": key, **value} for key, value in PROJECT_SCENARIOS.items()],
        "os_families": [{"value": key, "label": value} for key, value in OS_LABELS.items()],
        "management_interfaces": [{"value": key, "label": value} for key, value in INTERFACE_LABELS.items()],
        "defaults": {
            "monitor1_ip": "10.17.181.1", "cmk05_ip": "10.17.181.44",
            "whatsapp_host": "ws.2comconsulting.com.br", "vpn_dns_name": "vpn.oracledba.com.br",
            "monitor1_user": "jose.moraes",
        },
    }
