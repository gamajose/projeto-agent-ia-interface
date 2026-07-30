from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import Settings, get_settings
from app.services.playbooks import reload_playbooks
from app.services.tool_registry import describe_tools, resolve_tool


_PLAYBOOK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_STOPWORDS = {
    "para", "com", "sem", "uma", "das", "dos", "que", "por", "como", "servidor",
    "validar", "investigar", "identificar", "analisar", "alerta", "problema", "estado",
    "sobre", "entre", "onde", "quando", "esta", "esse", "essa", "isso", "depois",
}


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return normalized[:64].strip("-") or "playbook"


def _known_tools() -> dict[str, dict[str, Any]]:
    return {str(item["name"]): dict(item) for item in describe_tools()}


def _safe_profiles(values: list[str]) -> list[str]:
    profiles: list[str] = []
    for raw in values or ["any"]:
        value = str(raw or "").strip().lower()
        if not _PROFILE.fullmatch(value):
            raise ValueError(f"perfil inválido: {value!r}")
        if value not in profiles:
            profiles.append(value)
    return profiles or ["any"]


def _safe_patterns(values: list[str]) -> list[str]:
    patterns: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if len(value) > 500:
            raise ValueError("cada padrão deve ter no máximo 500 caracteres")
        try:
            re.compile(value, flags=re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"expressão regular inválida: {value}") from exc
        if value not in patterns:
            patterns.append(value)
    if not patterns:
        raise ValueError("informe ao menos um padrão de correspondência")
    return patterns


def _safe_text_list(values: list[str] | None, *, limit: int = 30, item_limit: int = 500) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value[:item_limit])
    return result[:limit]


def _parse_steps(steps_yaml: str) -> list[dict[str, Any]]:
    try:
        payload = yaml.safe_load(steps_yaml or "[]")
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML de etapas inválido: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("informe ao menos uma etapa estruturada")

    known = _known_tools()
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"etapa {index} precisa ser um objeto")
        if raw.get("command"):
            raise ValueError("a interface não aceita comandos shell em playbooks; use ferramentas estruturadas")
        tool = str(raw.get("tool") or "").strip()
        descriptor = known.get(tool)
        if not descriptor:
            raise ValueError(f"ferramenta desconhecida na etapa {index}: {tool}")
        if descriptor.get("correction"):
            raise ValueError("playbooks criados pela interface não podem incluir ferramentas corretivas")
        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError(f"arguments da etapa {index} precisa ser um objeto")
        purpose = str(raw.get("purpose") or descriptor.get("description") or tool).strip()[:500]
        if "{{" not in json.dumps(arguments, ensure_ascii=False, default=str):
            resolve_tool(tool, arguments)
        steps.append({"tool": tool, "arguments": arguments, "purpose": purpose})
    return steps[:20]


def save_playbook(
    *,
    playbook_id: str,
    title: str,
    priority: int,
    profiles: list[str],
    patterns: list[str],
    steps_yaml: str,
    summary: str = "",
    required_inputs: list[str] | None = None,
    safety_rules: list[str] | None = None,
    validation_notes: list[str] | None = None,
    import_notes: list[str] | None = None,
    source_filename: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    normalized_id = str(playbook_id or "").strip().lower()
    if not _PLAYBOOK_ID.fullmatch(normalized_id):
        raise ValueError("identificador deve usar letras minúsculas, números, hífen ou sublinhado")
    normalized_title = str(title or "").strip()
    if not 3 <= len(normalized_title) <= 160:
        raise ValueError("título deve ter entre 3 e 160 caracteres")
    normalized_priority = int(priority)
    if not 0 <= normalized_priority <= 999:
        raise ValueError("prioridade deve estar entre 0 e 999")

    payload = {
        "id": normalized_id,
        "title": normalized_title,
        "priority": normalized_priority,
        "profiles": _safe_profiles(profiles),
        "match": {"any": _safe_patterns(patterns)},
        "summary": str(summary or "").strip()[:4000],
        "required_inputs": _safe_text_list(required_inputs, item_limit=160),
        "safety_rules": _safe_text_list(safety_rules, item_limit=400),
        "steps": _parse_steps(steps_yaml),
        "allowed_corrections": [],
        "validation": [],
        "validation_notes": _safe_text_list(validation_notes, item_limit=400),
        "import_notes": _safe_text_list(import_notes, item_limit=500),
    }
    if source_filename:
        payload["source"] = {"filename": Path(source_filename).name[:255]}

    directory = Path(settings.agent_playbook_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{normalized_id}.yml"
    if path.exists():
        raise FileExistsError(f"o playbook {normalized_id} já existe")

    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)
    temporary = directory / f".{normalized_id}.{os.getpid()}.tmp"
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    try:
        os.replace(temporary, path)
    except OSError:
        path.write_text(content, encoding="utf-8")
        temporary.unlink(missing_ok=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass

    reload_playbooks()
    return {
        "id": normalized_id,
        "title": normalized_title,
        "priority": normalized_priority,
        "profiles": payload["profiles"],
        "patterns": payload["match"]["any"],
        "steps_count": len(payload["steps"]),
        "file": path.name,
    }


def _objective_keywords(objective: str) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_.-]+", objective.casefold())
    result: list[str] = []
    for word in words:
        simple = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode("ascii")
        if len(simple) < 4 or simple in _STOPWORDS or simple.isdigit():
            continue
        if simple not in result:
            result.append(simple)
    return result[:5]


def _draft_steps(investigation: dict[str, Any]) -> list[dict[str, Any]]:
    known = _known_tools()
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for plan in investigation.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        for raw in plan.get("tools") or plan.get("commands") or []:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool") or "").strip()
            descriptor = known.get(tool)
            if not descriptor or descriptor.get("correction") or tool in seen:
                continue
            arguments = raw.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                if "{{" not in json.dumps(arguments, ensure_ascii=False, default=str):
                    resolve_tool(tool, arguments)
            except Exception:
                continue
            steps.append({"tool": tool, "arguments": arguments, "purpose": str(raw.get("purpose") or descriptor.get("description") or tool)[:500]})
            seen.add(tool)
            if len(steps) >= 8:
                return steps

    objective = str(investigation.get("objective") or "").casefold()
    defaults = ["system.basics"]
    if any(value in objective for value in ("memória", "memoria", "swap", "ram")):
        defaults.append("memory.swap")
    if any(value in objective for value in ("disco", "filesystem", "inode", "espaço", "espaco")):
        defaults.append("filesystem.usage")
    if any(value in objective for value in ("checkmk", "omd", "monitoramento")):
        defaults.append("checkmk.discover")
    if any(value in objective for value in ("vpn", "rota", "rede", "conectividade")):
        defaults.extend(["network.interfaces", "vpn.inspect"])
    for tool in defaults:
        if tool in seen or tool not in known:
            continue
        arguments = {"path": "/"} if tool == "filesystem.usage" else {}
        steps.append({"tool": tool, "arguments": arguments, "purpose": known[tool]["description"]})
        seen.add(tool)
    return steps


def draft_playbook(investigation: dict[str, Any]) -> dict[str, Any]:
    for plan in investigation.get("plans") or []:
        candidate = plan.get("playbook") if isinstance(plan, dict) else None
        if isinstance(candidate, dict) and candidate.get("id"):
            raise ValueError(f"a investigação já utilizou o playbook {candidate['id']}")

    objective = str(investigation.get("objective") or "investigação operacional").strip()
    profile = str(investigation.get("profile") or "linux_generic").strip().lower()
    keywords = _objective_keywords(objective)
    suffix = "-".join(keywords[:3]) or "investigacao"
    playbook_id = _slug(f"{profile}-{suffix}")[:64]
    patterns = ["(?=.*" + ")(?=.*".join(re.escape(item) for item in keywords[:3]) + ")"] if keywords else [re.escape(objective[:80])]
    steps = _draft_steps(investigation) or [{"tool": "system.basics", "arguments": {}, "purpose": "Identificar o host e o estado básico."}]
    return {
        "id": playbook_id,
        "title": f"Diagnóstico: {objective[:110]}",
        "priority": 20,
        "profiles": [profile],
        "patterns": patterns,
        "steps_yaml": yaml.safe_dump(steps, allow_unicode=True, sort_keys=False, width=110),
        "source_investigation_id": investigation.get("id"),
    }
