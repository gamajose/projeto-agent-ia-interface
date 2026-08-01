from __future__ import annotations

import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.metrics import increment, observe
from app.services.redaction import redact_object, redact_text
from app.services.ssh import SSHExecutor


class OperationalToolError(ValueError):
    pass


@dataclass(frozen=True)
class OperationalToolDescriptor:
    name: str
    category: str
    description: str
    arguments: dict[str, str]
    requires_any: tuple[str, ...] = ()
    transport: str = "ssh"


_DESCRIPTORS = (
    OperationalToolDescriptor("checkmk.site.health", "monitoring", "Resume containers Checkmk, sites OMD e processos internos sem alterar serviços.", {} , ("docker", "podman")),
    OperationalToolDescriptor("checkmk.site.logs", "monitoring", "Lê logs recentes e filtrados de um site OMD.", {"site": "nome do site", "query": "termo opcional", "lines": "20-500"}),
    OperationalToolDescriptor("checkmk.status.host", "monitoring", "Consulta o estado de um host no Livestatus de todos os sites OMD disponíveis.", {"host": "hostname no Checkmk"}),
    OperationalToolDescriptor("checkmk.status.service", "monitoring", "Consulta no Livestatus um serviço específico de um host.", {"host": "hostname", "service": "descrição do serviço"}),
    OperationalToolDescriptor("checkmk.pending_changes", "monitoring", "Consulta mudanças pendentes na API REST do Checkmk usando somente GET.", {"base_url": "URL opcional", "site": "site opcional"}, transport="http"),
    OperationalToolDescriptor("checkmk.api.host", "monitoring", "Consulta a configuração de um host pela API REST do Checkmk usando somente GET.", {"host": "hostname", "base_url": "URL opcional", "site": "site opcional"}, transport="http"),
    OperationalToolDescriptor("checkmk.agent.output", "monitoring", "Coleta uma amostra limitada da saída local do agente Checkmk na porta 6556.", {"host": "IP/hostname opcional", "port": "porta, padrão 6556", "lines": "10-200"}),
    OperationalToolDescriptor("pfsense.gateway.status", "network", "Resume gateways, dpinger, latência e perda no pfSense/FreeBSD.", {}),
    OperationalToolDescriptor("pfsense.dpinger.logs", "network", "Lê eventos recentes de dpinger/gateways com filtro e limite.", {"query": "gateway opcional", "lines": "20-500"}),
    OperationalToolDescriptor("pfsense.openvpn.status", "network", "Resume processos, interfaces e logs recentes do OpenVPN.", {}),
    OperationalToolDescriptor("pfsense.ipsec.status", "network", "Resume Security Associations e logs recentes de IPsec.", {}),
    OperationalToolDescriptor("pfsense.routes", "network", "Lista rotas IPv4/IPv6 e rota padrão do pfSense/FreeBSD.", {}),
    OperationalToolDescriptor("pfsense.interfaces", "network", "Lista interfaces, endereços, estado e erros.", {}),
    OperationalToolDescriptor("pfsense.firewall.logs", "network", "Lê uma amostra limitada dos bloqueios recentes do firewall.", {"query": "IP/porta opcional", "lines": "20-500"}),
    OperationalToolDescriptor("vpn.flapping.timeline", "network", "Produz uma linha do tempo estruturada de eventos de gateway/VPN em uma janela limitada.", {"query": "gateway ou túnel", "minutes": "5-1440", "lines": "20-500"}),
    OperationalToolDescriptor("network.mtr", "network", "Executa MTR em modo relatório, ou traceroute/ping como fallback.", {"host": "IP/hostname", "count": "3-20"}, ("mtr", "traceroute", "ping")),
    OperationalToolDescriptor("network.traceroute", "network", "Executa traceroute limitado em saltos e tentativas.", {"host": "IP/hostname", "max_hops": "4-40"}, ("traceroute", "tracepath")),
    OperationalToolDescriptor("network.packet_capture", "network", "Captura somente cabeçalhos com filtro obrigatório, duração máxima de 30s e limite de pacotes.", {"filter": "filtro tcpdump permitido", "interface": "interface ou any", "seconds": "1-30", "packets": "10-1000"}, ("tcpdump",)),
    OperationalToolDescriptor("network.arp_neighbor", "network", "Lista vizinhos ARP/NDP e estados do cache.", {}, ("ip", "arp", "ndp")),
    OperationalToolDescriptor("network.conntrack_summary", "network", "Resume contagem e estados de conntrack quando disponíveis.", {}, ("conntrack",)),
    OperationalToolDescriptor("network.firewall_summary", "network", "Resume regras carregadas em nftables, iptables ou pf sem modificar políticas.", {}, ("nft", "iptables", "pfctl")),
    OperationalToolDescriptor("network.interface_errors", "network", "Coleta erros, drops, carrier e estatísticas das interfaces.", {}, ("ip", "netstat")),
    OperationalToolDescriptor("network.mtu_test", "network", "Testa MTU com ping sem fragmentação em tamanhos controlados.", {"host": "IP/hostname", "size": "576-8972"}, ("ping",)),
    OperationalToolDescriptor("network.dns_resolution", "network", "Compara resolução por getent e ferramentas DNS disponíveis.", {"host": "hostname"}, ("getent", "dig", "nslookup")),
    OperationalToolDescriptor("container.inspect", "container", "Resume estado, healthcheck, reinícios, imagem e timestamps do container.", {"container": "nome", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    OperationalToolDescriptor("container.logs", "container", "Lê logs recentes limitados e com timestamps.", {"container": "nome", "runtime": "auto|docker|podman", "lines": "20-500"}, ("docker", "podman")),
    OperationalToolDescriptor("container.events", "container", "Consulta eventos recentes do runtime em uma janela limitada.", {"container": "nome opcional", "minutes": "1-240"}, ("docker", "podman")),
    OperationalToolDescriptor("container.resources", "container", "Coleta uma amostra única de CPU, memória, PIDs e I/O.", {"container": "nome opcional", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    OperationalToolDescriptor("container.mounts", "container", "Lista mounts e volumes de um container sem ler dados internos.", {"container": "nome", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    OperationalToolDescriptor("container.health_history", "container", "Exibe o histórico limitado do healthcheck do container.", {"container": "nome", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    OperationalToolDescriptor("omd.status", "monitoring", "Consulta estado geral e processos de um site OMD.", {"site": "site opcional"}),
    OperationalToolDescriptor("omd.processes", "monitoring", "Lista processos pertencentes a um ou todos os sites OMD.", {"site": "site opcional"}),
    OperationalToolDescriptor("omd.logs", "monitoring", "Lê logs recentes de um site e componente, com limite.", {"site": "site", "component": "nome ou padrão", "lines": "20-500"}),
    OperationalToolDescriptor("omd.performance", "monitoring", "Resume processos, memória e arquivos de estado do site sem alteração.", {"site": "site"}),
    OperationalToolDescriptor("redfish.system.health", "hardware", "Consulta saúde geral, modelo, serial, BIOS e energia via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    OperationalToolDescriptor("redfish.power.supplies", "hardware", "Consulta fontes de alimentação via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    OperationalToolDescriptor("redfish.temperatures", "hardware", "Consulta temperaturas e limites via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    OperationalToolDescriptor("redfish.fans", "hardware", "Consulta ventiladores e rotações via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    OperationalToolDescriptor("redfish.storage", "hardware", "Consulta controladoras, volumes e discos via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    OperationalToolDescriptor("redfish.event.log", "hardware", "Consulta eventos recentes do hardware via Redfish GET.", {"base_url": "URL opcional", "limit": "10-200"}, transport="http"),
    OperationalToolDescriptor("redfish.network", "hardware", "Consulta interfaces de rede do BMC/sistema via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
)

_BY_NAME = {item.name: item for item in _DESCRIPTORS}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@:-]{1,255}$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
_SAFE_INTERFACE = re.compile(r"^(?:any|[A-Za-z0-9_.:-]{1,32})$")
_SAFE_QUERY = re.compile(r"^[\wÀ-ÿ ._:@/+-]{0,160}$", re.UNICODE)
_FORBIDDEN_CAPTURE = re.compile(r"(?:-w|--write-file|-G|-W|-z|-C|;|&&|\|\||`|\$\(|>|<)", re.I)
_ALLOWED_CAPTURE = re.compile(r"^[A-Za-z0-9_.:/ ()\[\]-]+$")


def describe_operational_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "category": item.category,
            "description": item.description,
            "correction": False,
            "arguments": dict(item.arguments),
            "requires_any": list(item.requires_any),
            "adaptive": True,
            "operational": True,
            "transport": item.transport,
        }
        for item in _DESCRIPTORS
    ]


def is_operational_tool(name: str) -> bool:
    return name in _BY_NAME


def _name(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_NAME.fullmatch(text) or text.startswith("-"):
        raise OperationalToolError(f"{field} inválido")
    return text


def _host(value: Any, field: str = "host") -> str:
    text = str(value or "").strip()
    if not _SAFE_HOST.fullmatch(text) or text.startswith("-"):
        raise OperationalToolError(f"{field} inválido")
    return text


def _query(value: Any, field: str = "query", *, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if (required and not text) or not _SAFE_QUERY.fullmatch(text):
        raise OperationalToolError(f"{field} inválido")
    return text


def _integer(value: Any, field: str, minimum: int, maximum: int, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise OperationalToolError(f"{field} inválido") from exc
    if not minimum <= result <= maximum:
        raise OperationalToolError(f"{field} deve estar entre {minimum} e {maximum}")
    return result


def _runtime(value: Any) -> str:
    runtime = str(value or "auto").strip().casefold()
    if runtime not in {"auto", "docker", "podman"}:
        raise OperationalToolError("runtime deve ser auto, docker ou podman")
    return runtime


def _runtime_command(runtime: str, docker: str, podman: str) -> str:
    if runtime == "docker":
        return docker
    if runtime == "podman":
        return podman
    return (
        f"if command -v docker >/dev/null 2>&1; then {docker}; "
        f"elif command -v podman >/dev/null 2>&1; then {podman}; "
        "else echo 'runtime de container indisponível'; exit 127; fi"
    )


def _site_loop(inner: str, site: str | None = None) -> str:
    selected = shlex.quote(site) if site else '"$s"'
    site_source = f"printf '%s\\n' {selected}" if site else "docker exec \"$c\" omd sites --bare 2>/dev/null"
    return (
        "for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -Ei 'checkmk|check-mk'); do "
        f"for s in $({site_source}); do echo \"CONTAINER=$c SITE=$s\"; "
        f"docker exec \"$c\" su - \"$s\" -c {shlex.quote(inner)} 2>&1; done; done"
    )


def _resolve_ssh(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
    if name == "checkmk.site.health":
        command = (
            "docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null | grep -Ei 'checkmk|check-mk' || true; "
            "for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -Ei 'checkmk|check-mk'); do "
            "echo \"CONTAINER=$c\"; docker exec \"$c\" omd sites --bare 2>/dev/null | while read s; do "
            "echo \"SITE=$s\"; docker exec \"$c\" su - \"$s\" -c 'omd status' 2>&1; done; done"
        )
        return command, True, 120, "resumir saúde dos containers e sites Checkmk"
    if name == "checkmk.site.logs":
        site = _name(args.get("site"), "site")
        lines = _integer(args.get("lines"), "lines", 20, 500, 150)
        query = _query(args.get("query"))
        grep = f" | grep -Fi -- {shlex.quote(query)}" if query else ""
        inner = f"find ~/var/log -maxdepth 1 -type f -print0 2>/dev/null | xargs -0 tail -n {lines} 2>/dev/null{grep} | tail -n {lines}"
        return _site_loop(inner, site), True, 120, f"ler logs recentes do site {site}"
    if name in {"checkmk.status.host", "checkmk.status.service"}:
        host = _name(args.get("host"), "host")
        service = _query(args.get("service"), "service", required=name.endswith("service"))
        query = ["GET hosts" if name.endswith("host") else "GET services"]
        query.append("Columns: name state plugin_output" if name.endswith("host") else "Columns: host_name description state plugin_output")
        query.append(f"Filter: name = {host}" if name.endswith("host") else f"Filter: host_name = {host}")
        if service:
            query.append(f"Filter: description = {service}")
        livestatus = "\\n".join(query) + "\\n"
        inner = f"printf {shlex.quote(livestatus)} | lq 2>/dev/null"
        return _site_loop(inner), True, 90, f"consultar estado Checkmk de {host}"
    if name == "checkmk.agent.output":
        host = _host(args.get("host") or "127.0.0.1")
        port = _integer(args.get("port"), "port", 1, 65535, 6556)
        lines = _integer(args.get("lines"), "lines", 10, 200, 80)
        command = f"timeout 15 bash -c {shlex.quote(f'exec 3<>/dev/tcp/{host}/{port}; head -n {lines} <&3')}"
        return command, False, 20, f"coletar amostra do agente Checkmk em {host}:{port}"
    if name == "pfsense.gateway.status":
        command = "pfSsh.php playback gatewaystatus 2>/dev/null || true; pgrep -alf dpinger || true; netstat -rn -f inet 2>/dev/null | head -n 80"
        return command, True, 45, "resumir gateways e processos dpinger"
    if name == "pfsense.dpinger.logs":
        lines = _integer(args.get("lines"), "lines", 20, 500, 200)
        query = _query(args.get("query"))
        grep = f" | grep -Fi -- {shlex.quote(query)}" if query else ""
        command = f"for f in /var/log/gateways.log /var/log/gateways.log.0; do [ -r \"$f\" ] && tail -n {lines} \"$f\"; done{grep} | tail -n {lines}"
        return command, True, 45, "ler eventos recentes de dpinger"
    if name == "pfsense.openvpn.status":
        command = "pgrep -alf openvpn || true; ifconfig -a 2>/dev/null | grep -E '^[a-zA-Z0-9].*:|status:|inet ' | head -n 200; tail -n 200 /var/log/openvpn.log 2>/dev/null || true"
        return command, True, 60, "resumir OpenVPN, interfaces e logs"
    if name == "pfsense.ipsec.status":
        command = "ipsec statusall 2>/dev/null || strongswan statusall 2>/dev/null || setkey -D 2>/dev/null || true; tail -n 200 /var/log/ipsec.log 2>/dev/null || true"
        return command, True, 60, "resumir estado e logs do IPsec"
    if name == "pfsense.routes":
        return "netstat -rn -f inet 2>/dev/null; netstat -rn -f inet6 2>/dev/null | head -n 200", True, 30, "listar rotas do firewall"
    if name == "pfsense.interfaces":
        return "ifconfig -a 2>/dev/null; netstat -i -b 2>/dev/null", True, 45, "listar estado e erros das interfaces"
    if name == "pfsense.firewall.logs":
        lines = _integer(args.get("lines"), "lines", 20, 500, 150)
        query = _query(args.get("query"))
        grep = f" | grep -Fi -- {shlex.quote(query)}" if query else ""
        command = f"clog /var/log/filter.log 2>/dev/null | tail -n {lines}{grep} | tail -n {lines}"
        return command, True, 45, "ler amostra dos eventos recentes do firewall"
    if name == "vpn.flapping.timeline":
        query = _query(args.get("query"), required=True)
        minutes = _integer(args.get("minutes"), "minutes", 5, 1440, 60)
        lines = _integer(args.get("lines"), "lines", 20, 500, 300)
        command = (
            f"query={shlex.quote(query)}; cutoff=$(date -d '-{minutes} minutes' '+%b %e %H:%M' 2>/dev/null || true); "
            f"for f in /var/log/gateways.log /var/log/openvpn.log /var/log/ipsec.log /var/log/messages; do "
            f"[ -r \"$f\" ] && tail -n 5000 \"$f\"; done | grep -Fi -- \"$query\" | tail -n {lines} | "
            "awk 'BEGIN{up=0;down=0;loss=0} {l=tolower($0); if(l~/clear|online|up/)up++; if(l~/alarm|down|offline/)down++; if(l~/loss/)loss++; print \"EVENT|\"$0} END{print \"SUMMARY|up=\"up\"|down=\"down\"|loss_events=\"loss}'"
        )
        return command, True, 60, f"construir linha do tempo de flapping para {query}"
    if name in {"network.mtr", "network.traceroute"}:
        host = _host(args.get("host"))
        if name == "network.mtr":
            count = _integer(args.get("count"), "count", 3, 20, 10)
            command = f"if command -v mtr >/dev/null 2>&1; then timeout 90 mtr -r -n -c {count} {shlex.quote(host)}; elif command -v traceroute >/dev/null 2>&1; then timeout 60 traceroute -n -w 2 -q 1 {shlex.quote(host)}; else ping -c {min(count, 10)} -W 2 {shlex.quote(host)}; fi"
            return command, False, 100, f"medir caminho e perda até {host}"
        hops = _integer(args.get("max_hops"), "max_hops", 4, 40, 20)
        command = f"if command -v traceroute >/dev/null 2>&1; then timeout 60 traceroute -n -m {hops} -w 2 -q 1 {shlex.quote(host)}; else timeout 60 tracepath -n -m {hops} {shlex.quote(host)}; fi"
        return command, False, 70, f"traçar caminho até {host}"
    if name == "network.packet_capture":
        interface = str(args.get("interface") or "any").strip()
        if not _SAFE_INTERFACE.fullmatch(interface):
            raise OperationalToolError("interface inválida")
        seconds = _integer(args.get("seconds"), "seconds", 1, 30, 10)
        packets = _integer(args.get("packets"), "packets", 10, 1000, 200)
        capture_filter = " ".join(str(args.get("filter") or "").strip().split())
        if not capture_filter or _FORBIDDEN_CAPTURE.search(capture_filter) or not _ALLOWED_CAPTURE.fullmatch(capture_filter):
            raise OperationalToolError("filtro tcpdump obrigatório ou não permitido")
        command = f"timeout {seconds} tcpdump -nn -tttt -s 128 -c {packets} -i {shlex.quote(interface)} {capture_filter} 2>&1 | head -n {packets + 20}"
        return command, True, seconds + 10, "capturar cabeçalhos de rede com limites rígidos"
    if name == "network.arp_neighbor":
        command = "if command -v ip >/dev/null 2>&1; then ip neigh show; elif command -v arp >/dev/null 2>&1; then arp -an; else ndp -an 2>/dev/null; fi"
        return command, True, 30, "listar vizinhos ARP e NDP"
    if name == "network.conntrack_summary":
        command = "conntrack -S 2>/dev/null || true; conntrack -L 2>/dev/null | awk '{print $1}' | sort | uniq -c | sort -nr | head -n 30"
        return command, True, 60, "resumir estados de conntrack"
    if name == "network.firewall_summary":
        command = "if command -v nft >/dev/null 2>&1; then nft list ruleset 2>/dev/null | head -n 500; elif command -v iptables-save >/dev/null 2>&1; then iptables-save 2>/dev/null | head -n 500; else pfctl -sr -v 2>/dev/null | head -n 500; fi"
        return command, True, 60, "resumir regras carregadas no firewall"
    if name == "network.interface_errors":
        command = "ip -s link 2>/dev/null || netstat -i -b 2>/dev/null; for f in /sys/class/net/*/statistics/{rx_errors,tx_errors,rx_dropped,tx_dropped}; do [ -r \"$f\" ] && echo \"$f=$(cat \"$f\")\"; done"
        return command, True, 45, "coletar erros e drops de interfaces"
    if name == "network.mtu_test":
        host = _host(args.get("host"))
        size = _integer(args.get("size"), "size", 576, 8972, 1472)
        command = f"ping -c 4 -W 2 -M do -s {size} {shlex.quote(host)} 2>&1 || ping -c 4 -W 2 -D -s {size} {shlex.quote(host)} 2>&1"
        return command, False, 30, f"testar MTU {size} até {host}"
    if name == "network.dns_resolution":
        host = _host(args.get("host"))
        quoted = shlex.quote(host)
        command = f"getent ahosts {quoted} 2>/dev/null || true; command -v dig >/dev/null 2>&1 && dig +time=3 +tries=1 {quoted} || true; command -v nslookup >/dev/null 2>&1 && nslookup {quoted} || true"
        return command, False, 30, f"comparar resolução DNS de {host}"
    if name.startswith("container."):
        container = _name(args.get("container"), "container") if name not in {"container.events", "container.resources"} or args.get("container") else ""
        runtime = _runtime(args.get("runtime"))
        quoted = shlex.quote(container) if container else ""
        if name == "container.inspect":
            docker = f"docker inspect --format {shlex.quote('{\"name\":{{json .Name}},\"image\":{{json .Config.Image}},\"state\":{{json .State}},\"restart_count\":{{json .RestartCount}},\"created\":{{json .Created}}}')} {quoted}"
            podman = f"podman inspect {quoted} --format json"
            return _runtime_command(runtime, docker, podman), True, 45, f"inspecionar container {container}"
        if name == "container.logs":
            lines = _integer(args.get("lines"), "lines", 20, 500, 150)
            return _runtime_command(runtime, f"docker logs --timestamps --tail {lines} {quoted} 2>&1", f"podman logs --timestamps --tail {lines} {quoted} 2>&1"), True, 60, f"ler logs do container {container}"
        if name == "container.events":
            minutes = _integer(args.get("minutes"), "minutes", 1, 240, 30)
            filter_arg = f"--filter container={quoted}" if container else ""
            docker = f"timeout 20 docker events --since {minutes}m --until 0s {filter_arg} 2>/dev/null | tail -n 300"
            podman = f"podman events --since {minutes}m --stream=false {filter_arg} 2>/dev/null | tail -n 300"
            return _runtime_command(runtime, docker, podman), True, 30, "consultar eventos recentes do runtime"
        if name == "container.resources":
            docker = f"docker stats --no-stream --format '{{{{.Name}}}}|{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}|{{{{.PIDs}}}}|{{{{.BlockIO}}}}' {quoted}"
            podman = f"podman stats --no-stream --format '{{{{.Name}}}}|{{{{.CPU}}}}|{{{{.MemUsage}}}}|{{{{.PIDs}}}}|{{{{.BlockInput}}}}/{{{{.BlockOutput}}}}' {quoted}"
            return _runtime_command(runtime, docker, podman), True, 45, "coletar recursos dos containers"
        if name == "container.mounts":
            docker = f"docker inspect --format {shlex.quote('{{json .Mounts}}')} {quoted}"
            podman = f"podman inspect --format {shlex.quote('{{json .Mounts}}')} {quoted}"
            return _runtime_command(runtime, docker, podman), True, 45, f"listar mounts do container {container}"
        if name == "container.health_history":
            docker = f"docker inspect --format {shlex.quote('{{json .State.Health.Log}}')} {quoted}"
            podman = f"podman inspect --format {shlex.quote('{{json .State.Healthcheck}}')} {quoted}"
            return _runtime_command(runtime, docker, podman), True, 45, f"consultar histórico de healthcheck de {container}"
    if name.startswith("omd."):
        site = _name(args.get("site"), "site") if args.get("site") else None
        if name == "omd.status":
            return _site_loop("omd status", site), True, 90, "consultar estado dos sites OMD"
        if name == "omd.processes":
            inner = "ps -u $(id -u) -o pid,ppid,stat,etime,%cpu,%mem,comm,args --sort=-%cpu | head -n 100"
            return _site_loop(inner, site), True, 90, "listar processos dos sites OMD"
        if name == "omd.logs":
            if not site:
                raise OperationalToolError("site é obrigatório")
            component = _name(args.get("component") or "*", "component") if args.get("component") not in {None, "", "*"} else "*"
            lines = _integer(args.get("lines"), "lines", 20, 500, 150)
            inner = f"find ~/var/log -maxdepth 1 -type f -name {shlex.quote(component + '*')} -print0 2>/dev/null | xargs -0 tail -n {lines} 2>/dev/null | tail -n {lines}"
            return _site_loop(inner, site), True, 120, f"ler logs OMD de {site}"
        if name == "omd.performance":
            if not site:
                raise OperationalToolError("site é obrigatório")
            inner = "echo PROCESSES; ps -u $(id -u) -o pid,stat,etime,%cpu,%mem,rss,comm --sort=-rss | head -n 80; echo TMPFS; df -h ~/tmp ~/var 2>/dev/null; echo CORE; omd status"
            return _site_loop(inner, site), True, 120, f"resumir desempenho do site {site}"
    raise OperationalToolError(f"ferramenta operacional desconhecida: {name}")


def _base_url(args: dict[str, Any], env_name: str) -> str:
    value = str(args.get("base_url") or os.getenv(env_name) or "").strip().rstrip("/")
    if not value.startswith(("https://", "http://")):
        raise OperationalToolError(f"{env_name} não configurada")
    return value


def _http_get(url: str, *, headers: dict[str, str] | None = None, auth: tuple[str, str] | None = None, verify: bool = True) -> tuple[int, Any, dict[str, str]]:
    with httpx.Client(timeout=15.0, verify=verify, follow_redirects=False) as client:
        response = client.get(url, headers=headers, auth=auth)
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:20000]
    else:
        body = response.text[:20000]
    allowed_headers = {key: value for key, value in response.headers.items() if key.casefold() in {"content-type", "etag", "date"}}
    return response.status_code, body, allowed_headers


def _checkmk_http(name: str, args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    base = _base_url(args, "CHECKMK_BASE_URL")
    site = str(args.get("site") or os.getenv("CHECKMK_SITE") or "").strip()
    if site and not _SAFE_NAME.fullmatch(site):
        raise OperationalToolError("site inválido")
    api_root = base if base.endswith("/check_mk/api/1.0") else f"{base}/{site}/check_mk/api/1.0" if site else f"{base}/check_mk/api/1.0"
    user = settings.checkmk_api_user or os.getenv("CHECKMK_API_USER")
    secret = settings.checkmk_api_secret or os.getenv("CHECKMK_API_SECRET")
    if not user or not secret:
        raise OperationalToolError("credenciais da API Checkmk não configuradas")
    headers = {"Authorization": f"Bearer {user} {secret}", "Accept": "application/json"}
    if name == "checkmk.api.host":
        host = _name(args.get("host"), "host")
        url = f"{api_root}/objects/host_config/{host}"
    else:
        url = f"{api_root}/domain-types/activation_run/collections/pending_changes"
    status, body, response_headers = _http_get(
        url,
        headers=headers,
        verify=os.getenv("CHECKMK_VERIFY_TLS", "true").casefold() not in {"0", "false", "no"},
    )
    return {"url": url, "http_status": status, "body": body, "headers": response_headers}


def _redfish_paths(name: str) -> list[str]:
    return {
        "redfish.system.health": ["/redfish/v1/Systems/1", "/redfish/v1/Chassis/1", "/redfish/v1/Managers/1"],
        "redfish.power.supplies": ["/redfish/v1/Chassis/1/Power"],
        "redfish.temperatures": ["/redfish/v1/Chassis/1/Thermal"],
        "redfish.fans": ["/redfish/v1/Chassis/1/Thermal"],
        "redfish.storage": ["/redfish/v1/Systems/1/Storage"],
        "redfish.event.log": ["/redfish/v1/Managers/1/LogServices/EventLog/Entries"],
        "redfish.network": ["/redfish/v1/Systems/1/EthernetInterfaces", "/redfish/v1/Managers/1/EthernetInterfaces"],
    }[name]


def _redfish_http(name: str, args: dict[str, Any]) -> dict[str, Any]:
    base = _base_url(args, "REDFISH_BASE_URL")
    username = os.getenv("REDFISH_USERNAME")
    password = os.getenv("REDFISH_PASSWORD")
    if not username or not password:
        raise OperationalToolError("REDFISH_USERNAME/REDFISH_PASSWORD não configurados")
    verify = os.getenv("REDFISH_VERIFY_TLS", "true").casefold() not in {"0", "false", "no"}
    results = []
    for path in _redfish_paths(name):
        status, body, headers = _http_get(base + path, auth=(username, password), verify=verify)
        results.append({"path": path, "http_status": status, "body": body, "headers": headers})
    if name == "redfish.event.log":
        limit = _integer(args.get("limit"), "limit", 10, 200, 100)
        for result in results:
            body = result.get("body")
            if isinstance(body, dict) and isinstance(body.get("Members"), list):
                body["Members"] = body["Members"][:limit]
    return {"base_url": base, "responses": results}


def execute_operational_tool(
    executor: SSHExecutor,
    environment: EnvironmentType,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    args = dict(arguments or {})
    descriptor = _BY_NAME.get(name)
    if not descriptor:
        return {"tool": name, "arguments": args, "status": "blocked", "reason": "ferramenta operacional desconhecida", "exit_code": 255, "stdout": "", "stderr": "", "normalized": {}}
    started = time.monotonic()
    base = {"tool": name, "arguments": redact_object(args), "category": descriptor.category, "adaptive": True, "operational": True, "correction": False, "transport": descriptor.transport}
    increment("agent_operational_tool_executions", labels={"tool": name, "transport": descriptor.transport})
    try:
        if descriptor.transport == "http":
            if name.startswith("checkmk."):
                normalized = _checkmk_http(name, args, settings or get_settings())
            else:
                normalized = _redfish_http(name, args)
            statuses = []
            if "http_status" in normalized:
                statuses.append(int(normalized["http_status"]))
            statuses.extend(int(item.get("http_status") or 0) for item in normalized.get("responses") or [])
            successful = bool(statuses) and all(200 <= status < 300 for status in statuses)
            return {**base, "status": "executed" if successful else "failed", "exit_code": 0 if successful else 1, "stdout": json.dumps(redact_object(normalized), ensure_ascii=False, default=str), "stderr": "", "normalized": redact_object(normalized)}
        command, sudo, timeout, purpose = _resolve_ssh(name, args)
        result = executor.run_sudo(command, environment, timeout=timeout) if sudo else executor.run(command, environment, timeout=timeout)
        stdout = redact_text(result.stdout)
        normalized = _normalize_output(name, stdout)
        return {**base, "command": command, "purpose": purpose, "sudo": sudo, "status": "executed" if result.exit_code == 0 else "failed", "exit_code": result.exit_code, "stdout": stdout, "stderr": redact_text(result.stderr), "normalized": normalized}
    except OperationalToolError as exc:
        return {**base, "status": "blocked", "reason": str(exc), "exit_code": 255, "stdout": "", "stderr": "", "normalized": {}}
    except Exception as exc:
        return {**base, "status": "failed", "reason": f"{type(exc).__name__}: {exc}", "exit_code": 255, "stdout": "", "stderr": redact_text(str(exc)), "normalized": {}}
    finally:
        observe("agent_operational_tool_duration_seconds", time.monotonic() - started, labels={"tool": name, "transport": descriptor.transport})


def _normalize_output(name: str, output: str) -> dict[str, Any]:
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    normalized: dict[str, Any] = {"line_count": len(lines), "sample": lines[-50:]}
    if name == "vpn.flapping.timeline":
        events = [line[6:] for line in lines if line.startswith("EVENT|")]
        summary_line = next((line[8:] for line in reversed(lines) if line.startswith("SUMMARY|")), "")
        summary = {}
        for item in summary_line.split("|"):
            key, separator, value = item.partition("=")
            if separator:
                try:
                    summary[key] = int(value)
                except ValueError:
                    summary[key] = value
        normalized = {"events": events, "summary": summary, "event_count": len(events)}
    elif name in {"network.interface_errors", "network.conntrack_summary"}:
        counters = {}
        for line in lines:
            key, separator, value = line.partition("=")
            if separator and value.strip().isdigit():
                counters[key.strip()] = int(value.strip())
        normalized["counters"] = counters
    return redact_object(normalized)
