from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote as url_unquote, urlsplit


SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@,+\-~]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configura a instalação portátil do Agent IA")
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--example", required=True, type=Path)
    parser.add_argument("--omniroute-env", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--venv-dir", required=True, type=Path)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--allowed-networks", required=True)
    return parser.parse_args()


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
        value = value.replace(r"\n", "\n").replace(r'\"', '"').replace(r"\\", "\\")
    return value


def read_pairs(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = unquote(value)
    return values


def render_value(value: str) -> str:
    value = str(value)
    if SAFE_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', r'\"').replace("\n", r"\n")
    return f'"{escaped}"'


def write_pairs(path: Path, source_lines: list[str], updates: dict[str, str], section: str) -> None:
    positions: dict[str, int] = {}
    lines = list(source_lines)
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            positions[key] = index

    appended: list[str] = []
    for key, value in updates.items():
        rendered = f"{key}={render_value(value)}"
        if key in positions:
            lines[positions[key]] = rendered
        else:
            appended.append(rendered)

    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"# {section}")
        lines.extend(appended)

    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines).rstrip() + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def generated_secret(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def password_from_url(value: str) -> str:
    if not value or "://" not in value:
        return ""
    try:
        return url_unquote(urlsplit(value).password or "")
    except ValueError:
        return ""


def port_from_url(value: str) -> int | None:
    if not value or "://" not in value:
        return None
    try:
        return urlsplit(value).port
    except ValueError:
        return None


def keep_or_generate(values: dict[str, str], key: str, *, size: int = 32) -> tuple[str, bool]:
    current = values.get(key, "").strip()
    if current and current != "CHANGE_ME":
        return current, False
    return generated_secret(size), True


def validated_port(value: str | None, default: int, label: str = "serviço") -> str:
    raw = str(value or default).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"porta de {label} inválida: {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"porta de {label} deve estar entre 1 e 65535")
    return str(port)


def docker_command(*arguments: str, timeout: int = 12) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def container_running(container: str) -> bool:
    result = docker_command("inspect", "--format", "{{.State.Running}}", container)
    return bool(result and result.returncode == 0 and result.stdout.strip() == "true")


def container_published_port(container: str, internal_port: int) -> int | None:
    if not container_running(container):
        return None
    result = docker_command("port", container, f"{internal_port}/tcp")
    if not result or result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = re.search(r":(\d+)\s*$", line.strip())
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
    return None


def port_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def choose_service_port(
    *,
    current: dict[str, str],
    key: str,
    url_key: str,
    default: int,
    container: str,
    internal_port: int,
    span: int = 100,
) -> tuple[int, bool]:
    published = container_published_port(container, internal_port)
    if published is not None:
        return published, False

    candidates: list[int] = []
    raw = current.get(key, "").strip()
    if raw.isdigit():
        candidates.append(int(raw))
    url_port = port_from_url(current.get(url_key, ""))
    if url_port:
        candidates.append(url_port)
    candidates.append(default)

    seen: set[int] = set()
    for candidate in candidates:
        if candidate in seen or not 1 <= candidate <= 65535:
            continue
        seen.add(candidate)
        if port_available(candidate):
            return candidate, candidate != default

    for candidate in range(default + 1, min(default + span, 65535) + 1):
        if candidate in seen:
            continue
        if port_available(candidate):
            return candidate, True

    raise RuntimeError(
        f"nenhuma porta livre encontrada entre {default} e {min(default + span, 65535)}"
    )


def main() -> None:
    args = parse_args()
    if args.env.exists():
        base_lines = args.env.read_text(encoding="utf-8").splitlines()
    elif args.example.exists():
        base_lines = args.example.read_text(encoding="utf-8").splitlines()
    else:
        base_lines = []

    current = read_pairs(base_lines)

    postgres_password = (
        current.get("POSTGRES_PASSWORD", "").strip()
        or password_from_url(current.get("POSTGRES_DSN", ""))
    )
    redis_password = (
        current.get("REDIS_PASSWORD", "").strip()
        or password_from_url(current.get("REDIS_URL", ""))
    )
    postgres_created = not bool(postgres_password and postgres_password != "CHANGE_ME")
    redis_created = not bool(redis_password and redis_password != "CHANGE_ME")
    if postgres_created:
        postgres_password = generated_secret(28)
    if redis_created:
        redis_password = generated_secret(28)

    postgres_port, postgres_port_changed = choose_service_port(
        current=current,
        key="POSTGRES_PORT",
        url_key="POSTGRES_DSN",
        default=5432,
        container="agent-ia-postgres",
        internal_port=5432,
    )
    redis_port, redis_port_changed = choose_service_port(
        current=current,
        key="REDIS_PORT",
        url_key="REDIS_URL",
        default=6379,
        container="agent-ia-redis",
        internal_port=6379,
    )

    approval_secret, approval_created = keep_or_generate(current, "APPROVAL_SECRET", size=48)
    api_token, api_created = keep_or_generate(current, "AGENT_API_TOKEN", size=36)

    ssh_password = os.environ.get("INSTALL_SSH_PASSWORD", "")
    bastion_password = os.environ.get("INSTALL_BASTION_PASSWORD", "")
    bastion_host = os.environ.get("INSTALL_BASTION_HOST", "").strip()
    bastion_port = os.environ.get("INSTALL_BASTION_PORT", "22").strip() or "22"
    bastion_user = os.environ.get("INSTALL_BASTION_USER", "").strip()

    app_dir = args.app_dir.resolve()
    install_root = args.install_root.resolve()
    venv_dir = args.venv_dir.resolve()
    registry_path = install_root / "data" / "providers.json"
    omniroute_port = validated_port(current.get("OMNIROUTE_PORT"), 20128, "OmniRoute")

    updates = {
        "APP_ENV": "production",
        "POSTGRES_PASSWORD": postgres_password,
        "REDIS_PASSWORD": redis_password,
        "POSTGRES_PORT": str(postgres_port),
        "REDIS_PORT": str(redis_port),
        "POSTGRES_DSN": f"postgresql+psycopg://agent_ia:{quote(postgres_password, safe='')}@127.0.0.1:{postgres_port}/agent_ia",
        "REDIS_URL": f"redis://:{quote(redis_password, safe='')}@127.0.0.1:{redis_port}/1",
        "APPROVAL_SECRET": approval_secret,
        "AGENT_API_TOKEN": api_token,
        "AGENT_INSTALL_ROOT": str(install_root),
        "AGENT_VENV_DIR": str(venv_dir),
        "AGENT_PLAYBOOK_DIR": str(app_dir / "config" / "playbooks"),
        "AGENT_UI_OPERATOR_NAME": args.operator,
        "AGENT_UI_HOST": "0.0.0.0",
        "AGENT_UI_PORT": current.get("AGENT_UI_PORT", "8080") or "8080",
        "AGENT_UI_ENABLED": "true",
        "AGENT_UI_ALLOWED_NETWORKS": args.allowed_networks,
        "SSH_DEFAULT_USER": args.ssh_user,
        "AI_PROVIDER_REGISTRY_PATH": str(registry_path),
        "AI_SETTINGS_ENV_PATH": str(args.env.resolve()),
        "OMNIROUTE_IMAGE": current.get("OMNIROUTE_IMAGE", "diegosouzapw/omniroute:latest") or "diegosouzapw/omniroute:latest",
        "OMNIROUTE_BIND_ADDRESS": current.get("OMNIROUTE_BIND_ADDRESS", "127.0.0.1") or "127.0.0.1",
        "OMNIROUTE_PORT": omniroute_port,
        "OMNIROUTE_ENV_FILE": str(args.omniroute_env.resolve()),
        "OMNIROUTE_BASE_URL": f"http://127.0.0.1:{omniroute_port}/v1",
        "CODEX_WORKDIR": str(app_dir),
        "OPENCODE_WORKDIR": str(app_dir),
    }
    if ssh_password:
        updates["SSH_DEFAULT_PASSWORD"] = ssh_password
    if bastion_host:
        updates.update(
            {
                "SSH_SRV_VPN_IP": bastion_host,
                "SSH_SRV_VPN_PORT": bastion_port,
                "SSH_SRV_VPN_USER": bastion_user,
            }
        )
        if bastion_password:
            updates["SSH_SRV_VPN_SENHA"] = bastion_password

    write_pairs(args.env, base_lines, updates, "Instalação portátil gerenciada")

    omni_lines = args.omniroute_env.read_text(encoding="utf-8").splitlines() if args.omniroute_env.exists() else []
    omni_current = read_pairs(omni_lines)
    initial_password = os.environ.get("INSTALL_OMNIROUTE_PASSWORD", "").strip()
    initial_created = False
    if not initial_password:
        initial_password = omni_current.get("INITIAL_PASSWORD", "").strip()
    if not initial_password:
        initial_password = generated_secret(24)
        initial_created = True

    omni_updates: dict[str, str] = {
        "JWT_SECRET": omni_current.get("JWT_SECRET", "").strip() or generated_secret(48),
        "INITIAL_PASSWORD": initial_password,
        "API_KEY_SECRET": omni_current.get("API_KEY_SECRET", "").strip() or generated_secret(48),
        "STORAGE_ENCRYPTION_KEY": omni_current.get("STORAGE_ENCRYPTION_KEY", "").strip() or generated_secret(48),
        "STORAGE_ENCRYPTION_KEY_VERSION": omni_current.get("STORAGE_ENCRYPTION_KEY_VERSION", "v1") or "v1",
        "MACHINE_ID_SALT": omni_current.get("MACHINE_ID_SALT", "").strip() or generated_secret(32),
        "PORT": "20128",
        "NODE_ENV": "production",
        "HOSTNAME": "0.0.0.0",
        "DATA_DIR": "/app/data",
        "STORAGE_DRIVER": "sqlite",
        "APP_LOG_TO_FILE": "true",
        "AUTH_COOKIE_SECURE": "false",
        "REQUIRE_API_KEY": omni_current.get("REQUIRE_API_KEY", "false") or "false",
    }
    write_pairs(args.omniroute_env, omni_lines, omni_updates, "OmniRoute local persistente")

    print(
        json.dumps(
            {
                "postgres_password_created": postgres_created,
                "redis_password_created": redis_created,
                "approval_secret_created": approval_created,
                "api_token_created": api_created,
                "omniroute_password_created": initial_created,
                "postgres_port": postgres_port,
                "redis_port": redis_port,
                "postgres_port_changed": postgres_port_changed,
                "redis_port_changed": redis_port_changed,
                "omniroute_port": int(omniroute_port),
                "env": str(args.env),
                "omniroute_env": str(args.omniroute_env),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
