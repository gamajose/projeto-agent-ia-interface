from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

from app.core.policies import EnvironmentType
from app.services.redaction import redact_text
from app.services.ssh import SSHExecutor


class AdaptiveToolError(ValueError):
    pass


@dataclass(frozen=True)
class AdaptiveToolDescriptor:
    name: str
    category: str
    description: str
    arguments: dict[str, str]
    requires_any: tuple[str, ...] = ()


_DESCRIPTORS = (
    AdaptiveToolDescriptor(
        "runtime.snapshot",
        "discovery",
        "Descobre dinamicamente sistema operacional, init, executáveis, serviços, listeners, runtimes de container e filesystems do alvo.",
        {},
    ),
    AdaptiveToolDescriptor(
        "service.search",
        "service",
        "Procura serviços e unidades pelo termo informado sem depender de nomes previamente conhecidos.",
        {"query": "termo do serviço"},
    ),
    AdaptiveToolDescriptor(
        "process.search",
        "process",
        "Procura processos em execução pelo nome, argumento ou tecnologia observada.",
        {"query": "termo do processo"},
        ("ps",),
    ),
    AdaptiveToolDescriptor(
        "logs.search",
        "logs",
        "Pesquisa uma mensagem, erro ou identificador nos logs recentes disponíveis no host.",
        {"query": "texto literal", "minutes": "1-1440", "lines": "10-500"},
    ),
    AdaptiveToolDescriptor(
        "network.listeners",
        "network",
        "Lista listeners TCP e UDP, opcionalmente filtrando uma porta descoberta durante a investigação.",
        {"port": "porta opcional 1-65535"},
        ("ss", "netstat"),
    ),
    AdaptiveToolDescriptor(
        "network.resolve",
        "network",
        "Resolve um hostname usando as ferramentas realmente disponíveis no alvo.",
        {"host": "hostname"},
        ("getent", "dig", "nslookup"),
    ),
    AdaptiveToolDescriptor(
        "network.path",
        "network",
        "Investiga o caminho até um destino usando tracepath, traceroute ou ping como fallback.",
        {"host": "IP ou hostname"},
        ("tracepath", "traceroute", "ping"),
    ),
    AdaptiveToolDescriptor(
        "container.inventory",
        "container",
        "Descobre containers em Docker ou Podman sem controlar o ciclo de vida.",
        {},
        ("docker", "podman"),
    ),
    AdaptiveToolDescriptor(
        "container.logs",
        "container",
        "Lê logs recentes de um container descoberto, sem reiniciar, parar ou remover.",
        {"container": "nome do container", "lines": "10-500", "runtime": "auto|docker|podman"},
        ("docker", "podman"),
    ),
    AdaptiveToolDescriptor(
        "package.search",
        "package",
        "Procura pacotes instalados pelo termo informado usando o gerenciador disponível.",
        {"query": "nome ou parte do pacote"},
        ("rpm", "dpkg-query", "apk", "pacman"),
    ),
    AdaptiveToolDescriptor(
        "file.search",
        "filesystem",
        "Localiza arquivos de configuração ou logs por nome em diretórios operacionais, sem ler o conteúdo.",
        {"query": "parte do nome", "root": "/etc|/opt|/var/log|/usr/local"},
        ("find",),
    ),
)

_SAFE_TEXT = re.compile(r"^[\wÀ-ÿ.@:/ +\-]{1,160}$", re.UNICODE)
_SAFE_HOST = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
_SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
_ALLOWED_ROOTS = {"/etc", "/opt", "/var/log", "/usr/local"}


def describe_adaptive_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "category": item.category,
            "description": item.description,
            "correction": False,
            "arguments": dict(item.arguments),
            "requires_any": list(item.requires_any),
            "adaptive": True,
        }
        for item in _DESCRIPTORS
    ]


def is_adaptive_tool(name: str) -> bool:
    return any(item.name == name for item in _DESCRIPTORS)


def _text(value: Any, field: str) -> str:
    result = " ".join(str(value or "").strip().split())
    if not result or not _SAFE_TEXT.fullmatch(result):
        raise AdaptiveToolError(f"{field} inválido")
    return result


def _host(value: Any) -> str:
    result = str(value or "").strip()
    if not _SAFE_HOST.fullmatch(result) or result.startswith("-"):
        raise AdaptiveToolError("host inválido")
    return result


def _container(value: Any) -> str:
    result = str(value or "").strip()
    if not _SAFE_CONTAINER.fullmatch(result) or result.startswith("-"):
        raise AdaptiveToolError("container inválido")
    return result


def _bounded_int(value: Any, field: str, minimum: int, maximum: int, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AdaptiveToolError(f"{field} inválido") from exc
    if not minimum <= result <= maximum:
        raise AdaptiveToolError(f"{field} deve estar entre {minimum} e {maximum}")
    return result


def _port(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return _bounded_int(value, "porta", 1, 65535, 0)


def _runtime(value: Any) -> str:
    result = str(value or "auto").strip().lower()
    if result not in {"auto", "docker", "podman"}:
        raise AdaptiveToolError("runtime deve ser auto, docker ou podman")
    return result


def _snapshot_command() -> str:
    return r'''
set +e
printf 'SNAPSHOT_VERSION=1\n'
printf 'KERNEL=%s\n' "$(uname -srmo 2>/dev/null || uname -a 2>/dev/null)"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  printf 'OS_ID=%s\n' "${ID:-unknown}"
  printf 'OS_NAME=%s\n' "${PRETTY_NAME:-${NAME:-unknown}}"
fi
if command -v systemctl >/dev/null 2>&1; then printf 'INIT=systemd\n';
elif command -v rc-service >/dev/null 2>&1; then printf 'INIT=openrc\n';
else printf 'INIT=unknown\n'; fi
for d in /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin; do
  [ -d "$d" ] || continue
  for f in "$d"/*; do
    [ -f "$f" ] && [ -x "$f" ] && printf 'BIN=%s\n' "${f##*/}"
  done
done | sort -u | head -n 1200
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null |
    awk '{print "SERVICE=" $1 "|" $3 "|" $4}' | head -n 250
elif command -v rc-status >/dev/null 2>&1; then
  rc-status -a 2>/dev/null | sed 's/^[[:space:]]*/SERVICE=/' | head -n 250
fi
if command -v ss >/dev/null 2>&1; then
  ss -H -lntup 2>/dev/null | sed 's/^/LISTENER=/' | head -n 250
elif command -v netstat >/dev/null 2>&1; then
  netstat -lntup 2>/dev/null | sed '1,2d;s/^/LISTENER=/' | head -n 250
fi
if command -v docker >/dev/null 2>&1; then
  printf 'CONTAINER_RUNTIME=docker\n'
  docker ps -a --format 'CONTAINER=docker|{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null | head -n 150
fi
if command -v podman >/dev/null 2>&1; then
  printf 'CONTAINER_RUNTIME=podman\n'
  podman ps -a --format 'CONTAINER=podman|{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null | head -n 150
fi
df -PT 2>/dev/null | sed '1d;s/^/FILESYSTEM=/' | head -n 100
'''.strip()


def resolve_adaptive_tool(name: str, arguments: dict[str, Any] | None = None) -> tuple[str, bool, int, str]:
    args = arguments or {}
    if name == "runtime.snapshot":
        return _snapshot_command(), False, 45, "descobrir capacidades reais do alvo"
    if name == "service.search":
        query = _text(args.get("query"), "query")
        quoted = shlex.quote(query)
        command = (
            "if command -v systemctl >/dev/null 2>&1; then "
            f"systemctl list-units --type=service --all --no-pager --plain 2>/dev/null | grep -Fi -- {quoted} | head -n 80; "
            f"systemctl list-unit-files --type=service --no-pager 2>/dev/null | grep -Fi -- {quoted} | head -n 80; "
            "elif command -v rc-status >/dev/null 2>&1; then "
            f"rc-status -a 2>/dev/null | grep -Fi -- {quoted} | head -n 80; "
            "else "
            f"ps -ef | grep -Fi -- {quoted} | grep -v grep | head -n 80; fi"
        )
        return command, False, 30, f"procurar serviços relacionados a {query}"
    if name == "process.search":
        query = _text(args.get("query"), "query")
        command = (
            "ps -eo pid,ppid,user,stat,lstart,etime,%cpu,%mem,comm,args --sort=-%cpu 2>/dev/null | "
            f"grep -Fi -- {shlex.quote(query)} | grep -v '[g]rep -Fi' | head -n 80"
        )
        return command, False, 30, f"procurar processos relacionados a {query}"
    if name == "logs.search":
        query = _text(args.get("query"), "query")
        minutes = _bounded_int(args.get("minutes"), "minutes", 1, 1440, 120)
        lines = _bounded_int(args.get("lines"), "lines", 10, 500, 120)
        quoted = shlex.quote(query)
        command = (
            "if command -v journalctl >/dev/null 2>&1; then "
            f"journalctl --since '-{minutes} minutes' --no-pager 2>/dev/null | grep -Fi -- {quoted} | tail -n {lines}; "
            "else "
            f"for f in /var/log/messages /var/log/syslog /var/log/system.log; do [ -r \"$f\" ] && grep -Fi -- {quoted} \"$f\"; done | tail -n {lines}; fi"
        )
        return command, True, 60, f"pesquisar {query} nos logs recentes"
    if name == "network.listeners":
        port = _port(args.get("port"))
        filter_command = f" | grep -E '(:|\\]){port}[[:space:]]'" if port else ""
        command = (
            "if command -v ss >/dev/null 2>&1; then ss -lntup 2>/dev/null"
            f"{filter_command}; elif command -v netstat >/dev/null 2>&1; then netstat -lntup 2>/dev/null{filter_command}; "
            "else echo 'nenhuma ferramenta de sockets disponível'; fi"
        )
        purpose = f"listar listeners da porta {port}" if port else "listar listeners TCP e UDP"
        return command, True, 30, purpose
    if name == "network.resolve":
        host = _host(args.get("host"))
        quoted = shlex.quote(host)
        command = (
            f"if command -v getent >/dev/null 2>&1; then getent ahosts {quoted}; "
            f"elif command -v dig >/dev/null 2>&1; then dig +short {quoted}; "
            f"elif command -v nslookup >/dev/null 2>&1; then nslookup {quoted}; "
            "else echo 'nenhuma ferramenta DNS disponível'; fi"
        )
        return command, False, 30, f"resolver o hostname {host}"
    if name == "network.path":
        host = _host(args.get("host"))
        quoted = shlex.quote(host)
        command = (
            f"if command -v tracepath >/dev/null 2>&1; then timeout 30 tracepath -n {quoted}; "
            f"elif command -v traceroute >/dev/null 2>&1; then timeout 30 traceroute -n -w 2 -q 1 {quoted}; "
            f"elif command -v ping >/dev/null 2>&1; then ping -c 4 -W 2 {quoted}; "
            "else echo 'nenhuma ferramenta de caminho disponível'; fi"
        )
        return command, False, 40, f"investigar o caminho até {host}"
    if name == "container.inventory":
        command = (
            "found=0; "
            "if command -v docker >/dev/null 2>&1; then found=1; echo 'RUNTIME=docker'; docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' 2>/dev/null; fi; "
            "if command -v podman >/dev/null 2>&1; then found=1; echo 'RUNTIME=podman'; podman ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' 2>/dev/null; fi; "
            "[ \"$found\" -eq 1 ] || echo 'nenhum runtime de container disponível'"
        )
        return command, True, 45, "descobrir containers e runtimes disponíveis"
    if name == "container.logs":
        container = _container(args.get("container"))
        lines = _bounded_int(args.get("lines"), "lines", 10, 500, 150)
        runtime = _runtime(args.get("runtime"))
        quoted = shlex.quote(container)
        if runtime == "docker":
            command = f"docker logs --tail {lines} --timestamps {quoted} 2>&1"
        elif runtime == "podman":
            command = f"podman logs --tail {lines} --timestamps {quoted} 2>&1"
        else:
            command = (
                f"if command -v docker >/dev/null 2>&1 && docker inspect {quoted} >/dev/null 2>&1; then docker logs --tail {lines} --timestamps {quoted} 2>&1; "
                f"elif command -v podman >/dev/null 2>&1 && podman inspect {quoted} >/dev/null 2>&1; then podman logs --tail {lines} --timestamps {quoted} 2>&1; "
                "else echo 'container não encontrado nos runtimes disponíveis'; exit 1; fi"
            )
        return command, True, 60, f"ler logs recentes do container {container}"
    if name == "package.search":
        query = _text(args.get("query"), "query")
        quoted = shlex.quote(query)
        command = (
            f"if command -v rpm >/dev/null 2>&1; then rpm -qa | grep -Fi -- {quoted} | head -n 100; "
            f"elif command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${{binary:Package}}|${{Version}}|${{db:Status-Abbrev}}\\n' 2>/dev/null | grep -Fi -- {quoted} | head -n 100; "
            f"elif command -v apk >/dev/null 2>&1; then apk info -vv 2>/dev/null | grep -Fi -- {quoted} | head -n 100; "
            f"elif command -v pacman >/dev/null 2>&1; then pacman -Q 2>/dev/null | grep -Fi -- {quoted} | head -n 100; "
            "else echo 'gerenciador de pacotes não identificado'; fi"
        )
        return command, False, 45, f"procurar pacote relacionado a {query}"
    if name == "file.search":
        query = _text(args.get("query"), "query")
        root = str(args.get("root") or "/etc").strip()
        if root not in _ALLOWED_ROOTS:
            raise AdaptiveToolError("root não permitido")
        pattern = shlex.quote(f"*{query}*")
        command = f"find {shlex.quote(root)} -xdev -iname {pattern} -print 2>/dev/null | head -n 120"
        return command, True, 90, f"localizar arquivos relacionados a {query} em {root}"
    raise AdaptiveToolError(f"ferramenta adaptativa desconhecida: {name}")


def execute_adaptive_tool(
    executor: SSHExecutor,
    environment: EnvironmentType,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        command, sudo, timeout, purpose = resolve_adaptive_tool(name, arguments)
    except AdaptiveToolError as exc:
        return {
            "tool": name,
            "arguments": arguments or {},
            "status": "blocked",
            "reason": str(exc),
            "exit_code": 255,
            "stdout": "",
            "stderr": "",
            "normalized": {},
            "adaptive": True,
        }

    base = {
        "tool": name,
        "arguments": arguments or {},
        "command": command,
        "purpose": purpose,
        "category": next(item.category for item in _DESCRIPTORS if item.name == name),
        "sudo": sudo,
        "adaptive": True,
    }
    try:
        result = (
            executor.run_sudo(command, environment, timeout=timeout)
            if sudo
            else executor.run(command, environment, timeout=timeout)
        )
        return {
            **base,
            "status": "executed" if result.exit_code == 0 else "failed",
            "exit_code": result.exit_code,
            "stdout": redact_text(result.stdout),
            "stderr": redact_text(result.stderr),
            "normalized": {},
        }
    except Exception as exc:
        return {
            **base,
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "exit_code": 255,
            "stdout": "",
            "stderr": redact_text(str(exc)),
            "normalized": {},
        }
