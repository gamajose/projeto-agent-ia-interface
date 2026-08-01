from __future__ import annotations

import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any

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


def _descriptor(
    name: str,
    category: str,
    description: str,
    arguments: dict[str, str] | None = None,
    requires: tuple[str, ...] = (),
    transport: str = "ssh",
) -> OperationalToolDescriptor:
    return OperationalToolDescriptor(
        name,
        category,
        description,
        arguments or {},
        requires,
        transport,
    )


_DESCRIPTORS = (
    _descriptor("checkmk.site.health", "monitoring", "Resume containers Checkmk, sites OMD e processos internos sem alterar serviços.", requires=("docker", "podman")),
    _descriptor("checkmk.site.logs", "monitoring", "Lê logs recentes e filtrados de um site OMD.", {"site": "nome", "query": "termo opcional", "lines": "20-500"}),
    _descriptor("checkmk.status.host", "monitoring", "Consulta o estado de um host pelo Livestatus.", {"host": "hostname"}),
    _descriptor("checkmk.status.service", "monitoring", "Consulta um serviço específico pelo Livestatus.", {"host": "hostname", "service": "descrição"}),
    _descriptor("checkmk.pending_changes", "monitoring", "Consulta mudanças pendentes na API REST do Checkmk usando somente GET.", {"base_url": "URL opcional", "site": "site opcional"}, transport="http"),
    _descriptor("checkmk.api.host", "monitoring", "Consulta a configuração de um host pela API REST usando somente GET.", {"host": "hostname", "base_url": "URL opcional", "site": "site opcional"}, transport="http"),
    _descriptor("checkmk.agent.output", "monitoring", "Coleta amostra limitada da saída do agente Checkmk.", {"host": "IP/hostname", "port": "padrão 6556", "lines": "10-200"}),
    _descriptor("pfsense.gateway.status", "network", "Resume gateways, dpinger, latência e perda no pfSense/FreeBSD."),
    _descriptor("pfsense.dpinger.logs", "network", "Lê eventos recentes de dpinger/gateways.", {"query": "gateway opcional", "lines": "20-500"}),
    _descriptor("pfsense.openvpn.status", "network", "Resume processos, interfaces e logs recentes do OpenVPN."),
    _descriptor("pfsense.ipsec.status", "network", "Resume Security Associations e logs recentes de IPsec."),
    _descriptor("pfsense.routes", "network", "Lista rotas IPv4/IPv6 e rota padrão."),
    _descriptor("pfsense.interfaces", "network", "Lista interfaces, endereços, estado e erros."),
    _descriptor("pfsense.firewall.logs", "network", "Lê amostra limitada dos bloqueios recentes.", {"query": "IP/porta opcional", "lines": "20-500"}),
    _descriptor("vpn.flapping.timeline", "network", "Produz linha do tempo estruturada de eventos de gateway/VPN.", {"query": "gateway/túnel", "minutes": "5-1440", "lines": "20-500"}),
    _descriptor("network.mtr", "network", "Executa MTR em relatório, traceroute ou ping como fallback.", {"host": "IP/hostname", "count": "3-20"}, ("mtr", "traceroute", "ping")),
    _descriptor("network.traceroute", "network", "Executa traceroute limitado em saltos e tentativas.", {"host": "IP/hostname", "max_hops": "4-40"}, ("traceroute", "tracepath")),
    _descriptor("network.packet_capture", "network", "Captura somente cabeçalhos, com filtro obrigatório, duração e quantidade limitadas.", {"filter": "filtro tcpdump", "interface": "interface/any", "seconds": "1-30", "packets": "10-1000"}, ("tcpdump",)),
    _descriptor("network.arp_neighbor", "network", "Lista vizinhos ARP/NDP e estados do cache.", requires=("ip", "arp", "ndp")),
    _descriptor("network.conntrack_summary", "network", "Resume contagem e estados de conntrack.", requires=("conntrack",)),
    _descriptor("network.firewall_summary", "network", "Resume regras carregadas em nftables, iptables ou pf.", requires=("nft", "iptables", "pfctl")),
    _descriptor("network.interface_errors", "network", "Coleta erros, drops, carrier e estatísticas de interfaces.", requires=("ip", "netstat")),
    _descriptor("network.mtu_test", "network", "Testa MTU com ping sem fragmentação em tamanho controlado.", {"host": "IP/hostname", "size": "576-8972"}, ("ping",)),
    _descriptor("network.dns_resolution", "network", "Compara resolução pelas ferramentas DNS disponíveis.", {"host": "hostname"}, ("getent", "dig", "nslookup")),
    _descriptor("container.inspect", "container", "Resume estado, healthcheck, reinícios, imagem e timestamps.", {"container": "nome", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    _descriptor("container.logs", "container", "Lê logs recentes limitados e com timestamps.", {"container": "nome", "runtime": "auto|docker|podman", "lines": "20-500"}, ("docker", "podman")),
    _descriptor("container.events", "container", "Consulta eventos recentes do runtime.", {"container": "opcional", "runtime": "auto|docker|podman", "minutes": "1-240"}, ("docker", "podman")),
    _descriptor("container.resources", "container", "Coleta amostra única de CPU, memória, PIDs e I/O.", {"container": "opcional", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    _descriptor("container.mounts", "container", "Lista mounts e volumes sem ler dados internos.", {"container": "nome", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    _descriptor("container.health_history", "container", "Exibe histórico limitado do healthcheck.", {"container": "nome", "runtime": "auto|docker|podman"}, ("docker", "podman")),
    _descriptor("omd.status", "monitoring", "Consulta estado geral e processos de um site OMD.", {"site": "opcional"}),
    _descriptor("omd.processes", "monitoring", "Lista processos de um ou todos os sites OMD.", {"site": "opcional"}),
    _descriptor("omd.logs", "monitoring", "Lê logs recentes de um site e componente.", {"site": "nome", "component": "padrão", "lines": "20-500"}),
    _descriptor("omd.performance", "monitoring", "Resume processos, memória e estado do site.", {"site": "nome"}),
    _descriptor("redfish.system.health", "hardware", "Consulta saúde geral, modelo, serial, BIOS e energia via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    _descriptor("redfish.power.supplies", "hardware", "Consulta fontes de alimentação via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    _descriptor("redfish.temperatures", "hardware", "Consulta temperaturas e limites via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    _descriptor("redfish.fans", "hardware", "Consulta ventiladores e rotações via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    _descriptor("redfish.storage", "hardware", "Consulta controladoras, volumes e discos via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
    _descriptor("redfish.event.log", "hardware", "Consulta eventos recentes do hardware via Redfish GET.", {"base_url": "URL opcional", "limit": "10-200"}, transport="http"),
    _descriptor("redfish.network", "hardware", "Consulta interfaces de rede do BMC/sistema via Redfish GET.", {"base_url": "URL opcional"}, transport="http"),
)
_BY_NAME = {item.name: item for item in _DESCRIPTORS}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@:-]{1,255}$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
_SAFE_INTERFACE = re.compile(r"^(?:any|[A-Za-z0-9_.:-]{1,32})$")
_SAFE_QUERY = re.compile(r"^[\wÀ-ÿ ._:@/+-]{0,160}$", re.UNICODE)
_ALLOWED_CAPTURE = re.compile(r"^[A-Za-z0-9_.:/ ()\[\]-]+$")
_FORBIDDEN_CAPTURE = re.compile(r"(?:-w|--write-file|-G|-W|-z|-C|;|&&|\|\||`|\$\(|>|<)", re.I)


def describe_operational_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "category": item.category,
            "description": item.description,
            "arguments": dict(item.arguments),
            "requires_any": list(item.requires_any),
            "transport": item.transport,
            "correction": False,
            "adaptive": True,
            "operational": True,
        }
        for item in _DESCRIPTORS
    ]


def is_operational_tool(name: str) -> bool:
    return name in _BY_NAME


def _name(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not _SAFE_NAME.fullmatch(result) or result.startswith("-"):
        raise OperationalToolError(f"{field} inválido")
    return result


def _host(value: Any, field: str = "host") -> str:
    result = str(value or "").strip()
    if not _SAFE_HOST.fullmatch(result) or result.startswith("-"):
        raise OperationalToolError(f"{field} inválido")
    return result


def _query(value: Any, field: str = "query", *, required: bool = False) -> str:
    result = " ".join(str(value or "").strip().split())
    if (required and not result) or not _SAFE_QUERY.fullmatch(result):
        raise OperationalToolError(f"{field} inválido")
    return result


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
    result = str(value or "auto").strip().casefold()
    if result not in {"auto", "docker", "podman"}:
        raise OperationalToolError("runtime deve ser auto, docker ou podman")
    return result


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
    if site:
        source = f"printf '%s\\n' {shlex.quote(site)}"
    else:
        source = "docker exec \"$c\" omd sites --bare 2>/dev/null"
    return (
        "for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -Ei 'checkmk|check-mk'); do "
        f"for s in $({source}); do echo \"CONTAINER=$c SITE=$s\"; "
        f"docker exec \"$c\" su - \"$s\" -c {shlex.quote(inner)} 2>&1; done; done"
    )


def _container_arguments(name: str, args: dict[str, Any]) -> tuple[str, str, str]:
    optional = name in {"container.events", "container.resources"}
    raw = args.get("container")
    container = _name(raw, "container") if raw else ""
    if not optional and not container:
        raise OperationalToolError("container é obrigatório")
    return container, shlex.quote(container) if container else "", _runtime(args.get("runtime"))


def _resolve_checkmk(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
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
        return _site_loop(inner, site), True, 120, f"ler logs do site {site}"
    if name in {"checkmk.status.host", "checkmk.status.service"}:
        host = _name(args.get("host"), "host")
        service = _query(args.get("service"), "service", required=name.endswith("service"))
        query = ["GET hosts" if name.endswith("host") else "GET services"]
        query.append("Columns: name state plugin_output" if name.endswith("host") else "Columns: host_name description state plugin_output")
        query.append(f"Filter: name = {host}" if name.endswith("host") else f"Filter: host_name = {host}")
        if service:
            query.append(f"Filter: description = {service}")
        payload = "\\n".join(query) + "\\n"
        return _site_loop(f"printf {shlex.quote(payload)} | lq 2>/dev/null"), True, 90, f"consultar estado de {host}"
    host = _host(args.get("host") or "127.0.0.1")
    port = _integer(args.get("port"), "port", 1, 65535, 6556)
    lines = _integer(args.get("lines"), "lines", 10, 200, 80)
    command = f"timeout 15 bash -c {shlex.quote(f'exec 3<>/dev/tcp/{host}/{port}; head -n {lines} <&3')}"
    return command, False, 20, f"coletar amostra do agente em {host}:{port}"


def _resolve_pfsense(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
    if name == "pfsense.gateway.status":
        return "pfSsh.php playback gatewaystatus 2>/dev/null || true; pgrep -alf dpinger || true; netstat -rn -f inet 2>/dev/null | head -n 80", True, 45, "resumir gateways e dpinger"
    if name == "pfsense.dpinger.logs":
        lines = _integer(args.get("lines"), "lines", 20, 500, 200)
        query = _query(args.get("query"))
        grep = f" | grep -Fi -- {shlex.quote(query)}" if query else ""
        return f"for f in /var/log/gateways.log /var/log/gateways.log.0; do [ -r \"$f\" ] && tail -n {lines} \"$f\"; done{grep} | tail -n {lines}", True, 45, "ler eventos de dpinger"
    if name == "pfsense.openvpn.status":
        return "pgrep -alf openvpn || true; ifconfig -a 2>/dev/null | grep -E '^[a-zA-Z0-9].*:|status:|inet ' | head -n 200; tail -n 200 /var/log/openvpn.log 2>/dev/null || true", True, 60, "resumir OpenVPN"
    if name == "pfsense.ipsec.status":
        return "ipsec statusall 2>/dev/null || strongswan statusall 2>/dev/null || setkey -D 2>/dev/null || true; tail -n 200 /var/log/ipsec.log 2>/dev/null || true", True, 60, "resumir IPsec"
    if name == "pfsense.routes":
        return "netstat -rn -f inet 2>/dev/null; netstat -rn -f inet6 2>/dev/null | head -n 200", True, 30, "listar rotas"
    if name == "pfsense.interfaces":
        return "ifconfig -a 2>/dev/null; netstat -i -b 2>/dev/null", True, 45, "listar interfaces e erros"
    if name == "pfsense.firewall.logs":
        lines = _integer(args.get("lines"), "lines", 20, 500, 150)
        query = _query(args.get("query"))
        grep = f" | grep -Fi -- {shlex.quote(query)}" if query else ""
        return f"clog /var/log/filter.log 2>/dev/null | tail -n {lines}{grep} | tail -n {lines}", True, 45, "ler eventos recentes do firewall"
    query = _query(args.get("query"), required=True)
    lines = _integer(args.get("lines"), "lines", 20, 500, 300)
    command = (
        f"query={shlex.quote(query)}; for f in /var/log/gateways.log /var/log/openvpn.log /var/log/ipsec.log /var/log/messages; do "
        f"[ -r \"$f\" ] && tail -n 5000 \"$f\"; done | grep -Fi -- \"$query\" | tail -n {lines} | "
        "awk 'BEGIN{up=0;down=0;loss=0} {l=tolower($0); if(l~/clear|online|up/)up++; if(l~/alarm|down|offline/)down++; if(l~/loss/)loss++; print \"EVENT|\"$0} END{print \"SUMMARY|up=\"up\"|down=\"down\"|loss_events=\"loss}'"
    )
    return command, True, 60, f"construir linha do tempo de {query}"


def _resolve_network(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
    if name in {"network.mtr", "network.traceroute"}:
        host = _host(args.get("host"))
        if name == "network.mtr":
            count = _integer(args.get("count"), "count", 3, 20, 10)
            command = f"if command -v mtr >/dev/null 2>&1; then timeout 90 mtr -r -n -c {count} {shlex.quote(host)}; elif command -v traceroute >/dev/null 2>&1; then timeout 60 traceroute -n -w 2 -q 1 {shlex.quote(host)}; else ping -c {min(count, 10)} -W 2 {shlex.quote(host)}; fi"
            return command, False, 100, f"medir caminho e perda até {host}"
        hops = _integer(args.get("max_hops"), "max_hops", 4, 40, 20)
        return f"if command -v traceroute >/dev/null 2>&1; then timeout 60 traceroute -n -m {hops} -w 2 -q 1 {shlex.quote(host)}; else timeout 60 tracepath -n -m {hops} {shlex.quote(host)}; fi", False, 70, f"traçar caminho até {host}"
    if name == "network.packet_capture":
        interface = str(args.get("interface") or "any").strip()
        capture_filter = " ".join(str(args.get("filter") or "").strip().split())
        if not _SAFE_INTERFACE.fullmatch(interface):
            raise OperationalToolError("interface inválida")
        if not capture_filter or _FORBIDDEN_CAPTURE.search(capture_filter) or not _ALLOWED_CAPTURE.fullmatch(capture_filter):
            raise OperationalToolError("filtro tcpdump obrigatório ou não permitido")
        seconds = _integer(args.get("seconds"), "seconds", 1, 30, 10)
        packets = _integer(args.get("packets"), "packets", 10, 1000, 200)
        return f"timeout {seconds} tcpdump -nn -tttt -s 128 -c {packets} -i {shlex.quote(interface)} {capture_filter} 2>&1 | head -n {packets + 20}", True, seconds + 10, "capturar cabeçalhos com limites rígidos"
    if name == "network.arp_neighbor":
        return "if command -v ip >/dev/null 2>&1; then ip neigh show; elif command -v arp >/dev/null 2>&1; then arp -an; else ndp -an 2>/dev/null; fi", True, 30, "listar vizinhos"
    if name == "network.conntrack_summary":
        return "conntrack -S 2>/dev/null || true; conntrack -L 2>/dev/null | awk '{print $1}' | sort | uniq -c | sort -nr | head -n 30", True, 60, "resumir conntrack"
    if name == "network.firewall_summary":
        return "if command -v nft >/dev/null 2>&1; then nft list ruleset 2>/dev/null | head -n 500; elif command -v iptables-save >/dev/null 2>&1; then iptables-save 2>/dev/null | head -n 500; else pfctl -sr -v 2>/dev/null | head -n 500; fi", True, 60, "resumir regras carregadas"
    if name == "network.interface_errors":
        return "ip -s link 2>/dev/null || netstat -i -b 2>/dev/null; for f in /sys/class/net/*/statistics/rx_errors /sys/class/net/*/statistics/tx_errors /sys/class/net/*/statistics/rx_dropped /sys/class/net/*/statistics/tx_dropped; do [ -r \"$f\" ] && echo \"$f=$(cat \"$f\")\"; done", True, 45, "coletar erros e drops"
    if name == "network.mtu_test":
        host = _host(args.get("host"))
        size = _integer(args.get("size"), "size", 576, 8972, 1472)
        return f"ping -c 4 -W 2 -M do -s {size} {shlex.quote(host)} 2>&1 || ping -c 4 -W 2 -D -s {size} {shlex.quote(host)} 2>&1", False, 30, f"testar MTU {size} até {host}"
    host = _host(args.get("host"))
    quoted = shlex.quote(host)
    return f"getent ahosts {quoted} 2>/dev/null || true; command -v dig >/dev/null 2>&1 && dig +time=3 +tries=1 {quoted} || true; command -v nslookup >/dev/null 2>&1 && nslookup {quoted} || true", False, 30, f"comparar resolução de {host}"


def _resolve_container(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
    container, quoted, runtime = _container_arguments(name, args)
    if name == "container.inspect":
        template = '{"name":{{json .Name}},"image":{{json .Config.Image}},"state":{{json .State}},"restart_count":{{json .RestartCount}},"created":{{json .Created}}}'
        docker = f"docker inspect --format {shlex.quote(template)} {quoted}"
        podman = f"podman inspect {quoted} --format json"
        return _runtime_command(runtime, docker, podman), True, 45, f"inspecionar {container}"
    if name == "container.logs":
        lines = _integer(args.get("lines"), "lines", 20, 500, 150)
        return _runtime_command(runtime, f"docker logs --timestamps --tail {lines} {quoted} 2>&1", f"podman logs --timestamps --tail {lines} {quoted} 2>&1"), True, 60, f"ler logs de {container}"
    if name == "container.events":
        minutes = _integer(args.get("minutes"), "minutes", 1, 240, 30)
        filter_arg = f"--filter container={quoted}" if container else ""
        docker = f"timeout 20 docker events --since {minutes}m {filter_arg} 2>/dev/null | tail -n 300"
        podman = f"podman events --since {minutes}m --stream=false {filter_arg} 2>/dev/null | tail -n 300"
        return _runtime_command(runtime, docker, podman), True, 30, "consultar eventos recentes"
    if name == "container.resources":
        docker = f"docker stats --no-stream --format '{{{{.Name}}}}|{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}|{{{{.PIDs}}}}|{{{{.BlockIO}}}}' {quoted}"
        podman = f"podman stats --no-stream --format '{{{{.Name}}}}|{{{{.CPU}}}}|{{{{.MemUsage}}}}|{{{{.PIDs}}}}' {quoted}"
        return _runtime_command(runtime, docker, podman), True, 45, "coletar recursos"
    if name == "container.mounts":
        template = "{{json .Mounts}}"
    else:
        template = "{{json .State.Health.Log}}" if runtime != "podman" else "{{json .State.Healthcheck}}"
    docker = f"docker inspect --format {shlex.quote(template)} {quoted}"
    podman = f"podman inspect --format {shlex.quote(template)} {quoted}"
    purpose = "listar mounts" if name == "container.mounts" else "consultar histórico de healthcheck"
    return _runtime_command(runtime, docker, podman), True, 45, f"{purpose} de {container}"


def _resolve_omd(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
    site = _name(args.get("site"), "site") if args.get("site") else None
    if name == "omd.status":
        return _site_loop("omd status", site), True, 90, "consultar estado OMD"
    if name == "omd.processes":
        return _site_loop("ps -u $(id -u) -o pid,ppid,stat,etime,%cpu,%mem,comm,args --sort=-%cpu | head -n 100", site), True, 90, "listar processos OMD"
    if not site:
        raise OperationalToolError("site é obrigatório")
    if name == "omd.logs":
        component = _name(args.get("component"), "component") if args.get("component") else ""
        lines = _integer(args.get("lines"), "lines", 20, 500, 150)
        pattern = (component or "") + "*"
        inner = f"find ~/var/log -maxdepth 1 -type f -name {shlex.quote(pattern)} -print0 2>/dev/null | xargs -0 tail -n {lines} 2>/dev/null | tail -n {lines}"
        return _site_loop(inner, site), True, 120, f"ler logs de {site}"
    inner = "echo PROCESSES; ps -u $(id -u) -o pid,stat,etime,%cpu,%mem,rss,comm --sort=-rss | head -n 80; echo FILESYSTEMS; df -h ~/tmp ~/var 2>/dev/null; echo CORE; omd status"
    return _site_loop(inner, site), True, 120, f"resumir desempenho de {site}"


def _resolve_ssh(name: str, args: dict[str, Any]) -> tuple[str, bool, int, str]:
    if name.startswith("checkmk."):
        return _resolve_checkmk(name, args)
    if name.startswith("pfsense.") or name == "vpn.flapping.timeline":
        return _resolve_pfsense(name, args)
    if name.startswith("network."):
        return _resolve_network(name, args)
    if name.startswith("container."):
        return _resolve_container(name, args)
    if name.startswith("omd."):
        return _resolve_omd(name, args)
    raise OperationalToolError(f"ferramenta operacional desconhecida: {name}")


def _base_url(args: dict[str, Any], env_name: str) -> str:
    value = str(args.get("base_url") or os.getenv(env_name) or "").strip().rstrip("/")
    if not value.startswith(("https://", "http://")):
        raise OperationalToolError(f"{env_name} não configurada")
    return value


def _http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    verify: bool = True,
) -> tuple[int, Any, dict[str, str]]:
    with httpx.Client(timeout=15.0, verify=verify, follow_redirects=False) as client:
        response = client.get(url, headers=headers, auth=auth)
    try:
        body: Any = response.json() if "json" in response.headers.get("content-type", "") else response.text[:20000]
    except ValueError:
        body = response.text[:20000]
    allowed = {key: value for key, value in response.headers.items() if key.casefold() in {"content-type", "etag", "date"}}
    return response.status_code, body, allowed


def _checkmk_http(name: str, args: dict[str, Any], settings: Settings) -> dict[str, Any]:
    base = _base_url(args, "CHECKMK_BASE_URL")
    site = str(args.get("site") or os.getenv("CHECKMK_SITE") or "").strip()
    if site and not _SAFE_NAME.fullmatch(site):
        raise OperationalToolError("site inválido")
    if base.endswith("/check_mk/api/1.0"):
        api_root = base
    elif site:
        api_root = f"{base}/{site}/check_mk/api/1.0"
    else:
        api_root = f"{base}/check_mk/api/1.0"
    user = settings.checkmk_api_user or os.getenv("CHECKMK_API_USER")
    secret = settings.checkmk_api_secret or os.getenv("CHECKMK_API_SECRET")
    if not user or not secret:
        raise OperationalToolError("credenciais Checkmk não configuradas")
    headers = {"Authorization": f"Bearer {user} {secret}", "Accept": "application/json"}
    if name == "checkmk.api.host":
        url = f"{api_root}/objects/host_config/{_name(args.get('host'), 'host')}"
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
        raise OperationalToolError("credenciais Redfish não configuradas")
    verify = os.getenv("REDFISH_VERIFY_TLS", "true").casefold() not in {"0", "false", "no"}
    responses = []
    for path in _redfish_paths(name):
        status, body, headers = _http_get(base + path, auth=(username, password), verify=verify)
        responses.append({"path": path, "http_status": status, "body": body, "headers": headers})
    if name == "redfish.event.log":
        limit = _integer(args.get("limit"), "limit", 10, 200, 100)
        for response in responses:
            body = response.get("body")
            if isinstance(body, dict) and isinstance(body.get("Members"), list):
                body["Members"] = body["Members"][:limit]
    return {"base_url": base, "responses": responses}


def _normalize_output(name: str, output: str) -> dict[str, Any]:
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    if name == "vpn.flapping.timeline":
        events = [line[6:] for line in lines if line.startswith("EVENT|")]
        summary_line = next((line[8:] for line in reversed(lines) if line.startswith("SUMMARY|")), "")
        summary: dict[str, Any] = {}
        for item in summary_line.split("|"):
            key, separator, value = item.partition("=")
            if separator:
                summary[key] = int(value) if value.isdigit() else value
        return {"events": events, "summary": summary, "event_count": len(events)}
    counters = {}
    if name in {"network.interface_errors", "network.conntrack_summary"}:
        for line in lines:
            key, separator, value = line.partition("=")
            if separator and value.strip().isdigit():
                counters[key.strip()] = int(value.strip())
    return {"line_count": len(lines), "sample": lines[-50:], "counters": counters}


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
    if descriptor is None:
        return {"tool": name, "status": "blocked", "reason": "ferramenta desconhecida", "exit_code": 255, "stdout": "", "stderr": "", "normalized": {}}
    started = time.monotonic()
    base = {
        "tool": name,
        "arguments": redact_object(args),
        "category": descriptor.category,
        "transport": descriptor.transport,
        "correction": False,
        "adaptive": True,
        "operational": True,
    }
    increment("agent_operational_tool_executions", labels={"tool": name, "transport": descriptor.transport})
    try:
        if descriptor.transport == "http":
            normalized = _checkmk_http(name, args, settings or get_settings()) if name.startswith("checkmk.") else _redfish_http(name, args)
            statuses = [int(normalized.get("http_status") or 0)] if "http_status" in normalized else [int(item.get("http_status") or 0) for item in normalized.get("responses") or []]
            successful = bool(statuses) and all(200 <= status < 300 for status in statuses)
            return {
                **base,
                "status": "executed" if successful else "failed",
                "exit_code": 0 if successful else 1,
                "stdout": json.dumps(redact_object(normalized), ensure_ascii=False, default=str),
                "stderr": "",
                "normalized": redact_object(normalized),
            }
        command, sudo, timeout, purpose = _resolve_ssh(name, args)
        result = executor.run_sudo(command, environment, timeout=timeout) if sudo else executor.run(command, environment, timeout=timeout)
        stdout = redact_text(result.stdout)
        return {
            **base,
            "command": command,
            "purpose": purpose,
            "sudo": sudo,
            "status": "executed" if result.exit_code == 0 else "failed",
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": redact_text(result.stderr),
            "normalized": redact_object(_normalize_output(name, stdout)),
        }
    except OperationalToolError as exc:
        return {**base, "status": "blocked", "reason": str(exc), "exit_code": 255, "stdout": "", "stderr": "", "normalized": {}}
    except Exception as exc:
        return {**base, "status": "failed", "reason": f"{type(exc).__name__}: {exc}", "exit_code": 255, "stdout": "", "stderr": redact_text(str(exc)), "normalized": {}}
    finally:
        observe("agent_operational_tool_duration_seconds", time.monotonic() - started, labels={"tool": name, "transport": descriptor.transport})
