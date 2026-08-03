from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza o .env do Agent IA sem sobrescrever segredos existentes."
    )
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--example", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--venv-dir", required=True, type=Path)
    parser.add_argument("--omniroute-env", required=True, type=Path)
    return parser.parse_args()


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_pairs(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if KEY_RE.fullmatch(key):
            values[key] = unquote(value)
    return values


def render_value(value: str) -> str:
    value = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@,+\-~]*", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', r'\"').replace("\n", r"\n")
    return f'"{escaped}"'


def atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def set_value(lines: list[str], key: str, value: str) -> bool:
    rendered = f"{key}={render_value(value)}"
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith(f"{key}="):
            if raw == rendered:
                return False
            lines[index] = rendered
            return True
    lines.append(rendered)
    return True


def main() -> None:
    args = parse_args()
    env_path = args.env.resolve()
    example_path = args.example.resolve()
    app_dir = args.app_dir.resolve()
    install_root = args.install_root.resolve()
    venv_dir = args.venv_dir.resolve()
    omniroute_env = args.omniroute_env.resolve()

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    elif example_path.exists():
        lines = example_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    original = list(lines)
    current = parse_pairs(lines)
    example_lines = example_path.read_text(encoding="utf-8").splitlines() if example_path.exists() else []
    example = parse_pairs(example_lines)

    missing_keys: list[str] = []
    for key, value in example.items():
        if key not in current:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"{key}={render_value(value)}")
            current[key] = value
            missing_keys.append(key)

    aliases = {
        "GEMINI_API_KEY": ("GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY"),
        "GROQ_API_KEY": ("GROQ_KEY",),
        "DEEPSEEK_API_KEY": ("DEEPSEEK_KEY",),
        "OPENROUTER_API_KEY": ("OPENROUTER_KEY",),
    }
    migrated_aliases: list[str] = []
    current = parse_pairs(lines)
    for canonical, candidates in aliases.items():
        if current.get(canonical, "").strip():
            continue
        for alias in candidates:
            value = current.get(alias, "").strip()
            if value:
                set_value(lines, canonical, value)
                current[canonical] = value
                migrated_aliases.append(f"{alias}->{canonical}")
                break

    port = current.get("OMNIROUTE_PORT", "20128").strip() or "20128"
    managed_paths = {
        "AGENT_INSTALL_ROOT": str(install_root),
        "AGENT_VENV_DIR": str(venv_dir),
        "AGENT_PLAYBOOK_DIR": str(app_dir / "config" / "playbooks"),
        "AI_PROVIDER_REGISTRY_PATH": str(install_root / "data" / "providers.json"),
        "AI_SETTINGS_ENV_PATH": str(env_path),
        "OMNIROUTE_ENV_FILE": str(omniroute_env),
        "OMNIROUTE_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "CODEX_WORKDIR": str(app_dir),
        "OPENCODE_WORKDIR": str(app_dir),
    }
    path_updates: list[str] = []
    for key, value in managed_paths.items():
        if set_value(lines, key, value):
            path_updates.append(key)

    changed = lines != original
    backup = ""
    if changed and env_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = env_path.with_name(f"{env_path.name}.bkp-{timestamp}")
        shutil.copy2(env_path, backup_path)
        os.chmod(backup_path, 0o600)
        backup = str(backup_path)

    if changed or not env_path.exists():
        atomic_write(env_path, lines)

    final = parse_pairs(lines)
    recognized = [
        key
        for key in (
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENROUTER_API_KEY",
            "OMNIROUTE_API_KEY",
        )
        if final.get(key, "").strip()
    ]

    print(
        json.dumps(
            {
                "changed": changed,
                "backup": backup,
                "missing_keys_added": len(missing_keys),
                "migrated_aliases": migrated_aliases,
                "managed_paths_updated": path_updates,
                "recognized_api_keys": recognized,
                "env": str(env_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
