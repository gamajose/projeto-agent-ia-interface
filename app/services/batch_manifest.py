from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml


class BatchManifestError(ValueError):
    pass


_FIELD_ALIASES = {
    "target": "target",
    "alvo": "target",
    "ip": "target",
    "vpn_ip": "target",
    "ip_vpn": "target",
    "host": "hostname",
    "hostname": "hostname",
    "nome": "hostname",
    "servidor": "hostname",
    "site": "site",
    "porta": "ssh_port",
    "port": "ssh_port",
    "ssh_port": "ssh_port",
    "porta_ssh": "ssh_port",
    "ambiente": "environment",
    "environment": "environment",
    "modo": "mode",
    "mode": "mode",
    "objetivo": "objective",
    "objective": "objective",
    "problema": "objective",
    "provider": "provider",
    "provedor": "provider",
    "ia": "provider",
    "model": "model",
    "modelo": "model",
    "rota": "model",
    "playbook": "playbook_id",
    "playbook_id": "playbook_id",
    "modo_playbook": "playbook_mode",
    "playbook_mode": "playbook_mode",
}

_ENVIRONMENT_ALIASES = {
    "": "unknown",
    "unknown": "unknown",
    "desconhecido": "unknown",
    "nao_informado": "unknown",
    "prod": "production",
    "producao": "production",
    "production": "production",
    "standby": "standby",
    "std": "standby",
    "monitoramento": "monitoring",
    "monitor": "monitoring",
    "monitoring": "monitoring",
    "treinamento": "training",
    "training": "training",
    "lab": "training",
}

_MODE_ALIASES = {
    "": "propose",
    "propor": "propose",
    "propose": "propose",
    "investigar": "investigate",
    "investigate": "investigate",
    "validar": "investigate",
    "corrigir": "correct",
    "correct": "correct",
}

_PLAYBOOK_MODE_ALIASES = {
    "": "auto",
    "auto": "auto",
    "automatico": "auto",
    "manual": "manual",
    "none": "none",
    "nenhum": "none",
    "sem": "none",
}

_ALLOWED_PROVIDERS = {"auto", "gemini", "groq", "openrouter", "ollama", "omniroute"}


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _clean_text(value: Any, *, limit: int = 12000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise BatchManifestError(f"valor excede o limite de {limit} caracteres")
    return text


def _normalize_port(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise BatchManifestError(f"porta SSH inválida: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise BatchManifestError(f"porta SSH fora do intervalo permitido: {port}")
    return port


def _normalize_environment(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = _key(value)
    if normalized not in _ENVIRONMENT_ALIASES:
        raise BatchManifestError(f"ambiente inválido: {value!r}")
    return _ENVIRONMENT_ALIASES[normalized]


def _normalize_mode(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = _key(value)
    if normalized not in _MODE_ALIASES:
        raise BatchManifestError(f"modo operacional inválido: {value!r}")
    return _MODE_ALIASES[normalized]


def _normalize_playbook_mode(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = _key(value)
    if normalized not in _PLAYBOOK_MODE_ALIASES:
        raise BatchManifestError(f"modo de playbook inválido: {value!r}")
    return _PLAYBOOK_MODE_ALIASES[normalized]


def _normalize_provider(value: Any) -> str | None:
    if value in (None, ""):
        return None
    provider = _key(value)
    if provider not in _ALLOWED_PROVIDERS:
        raise BatchManifestError(f"provedor de IA inválido: {value!r}")
    return provider


def _canonical_mapping(value: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        canonical = _FIELD_ALIASES.get(_key(raw_key))
        if canonical:
            mapped[canonical] = raw_value
    return mapped


def _parse_target_token(token: str) -> dict[str, Any]:
    value = token.strip()
    if not value:
        raise BatchManifestError("alvo vazio")

    bracketed = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", value)
    if bracketed:
        return {"target": bracketed.group(1), "ssh_port": int(bracketed.group(2))}

    if value.count(":") == 1:
        host, possible_port = value.rsplit(":", 1)
        if possible_port.isdigit() and host.strip():
            return {"target": host.strip(), "ssh_port": int(possible_port)}

    if "|" in value:
        host, possible_port = value.rsplit("|", 1)
        if possible_port.strip().isdigit() and host.strip():
            return {"target": host.strip(), "ssh_port": int(possible_port.strip())}

    return {"target": value}


def _plain_targets(content: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for token in re.split(r"[;,]", line):
            token = token.strip()
            if token:
                rows.append(_parse_target_token(token))
    return rows


def _looks_like_table(content: str) -> bool:
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if not first:
        return False
    headers = {_FIELD_ALIASES.get(_key(item)) for item in re.split(r"[;,\t]", first)}
    return "target" in headers or "hostname" in headers or "site" in headers


def _tabular_targets(content: str) -> list[dict[str, Any]]:
    first = next((line for line in content.splitlines() if line.strip()), "")
    delimiter = max((";", ",", "\t"), key=lambda item: first.count(item))
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if not reader.fieldnames:
        raise BatchManifestError("arquivo tabular sem cabeçalho")
    return [dict(row) for row in reader if any(str(value or "").strip() for value in row.values())]


def _structured_targets(data: Any) -> tuple[dict[str, Any], list[Any]]:
    if isinstance(data, list):
        return {}, data
    if not isinstance(data, dict):
        raise BatchManifestError("o arquivo estruturado precisa conter uma lista ou objeto")

    defaults = data.get("defaults") or data.get("padroes") or {}
    if defaults and not isinstance(defaults, dict):
        raise BatchManifestError("defaults precisa ser um objeto")

    targets = (
        data.get("targets")
        or data.get("alvos")
        or data.get("hosts")
        or data.get("servidores")
    )
    if targets is None:
        if any(key in data for key in ("steps", "allowed_corrections", "validation_tools")):
            raise BatchManifestError(
                "o arquivo parece ser um playbook de diagnóstico, mas não contém targets/alvos para execução em lote"
            )
        if any(_FIELD_ALIASES.get(_key(key)) in {"target", "hostname", "site"} for key in data):
            targets = [data]
            defaults = {}
        else:
            raise BatchManifestError("o arquivo não contém a lista targets/alvos")

    if isinstance(targets, str):
        return dict(defaults), _plain_targets(targets)
    if not isinstance(targets, list):
        raise BatchManifestError("targets/alvos precisa ser uma lista")
    return dict(defaults), targets


def _load_manifest(filename: str, content: str) -> tuple[dict[str, Any], list[Any], str]:
    suffix = Path(filename or "lote.txt").suffix.casefold()
    if suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BatchManifestError(f"JSON inválido na linha {exc.lineno}: {exc.msg}") from exc
        defaults, targets = _structured_targets(data)
        return defaults, targets, "json"

    if suffix in {".yaml", ".yml"}:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise BatchManifestError(f"YAML inválido: {exc}") from exc
        defaults, targets = _structured_targets(data)
        return defaults, targets, "yaml"

    if suffix == ".csv" or _looks_like_table(content):
        return {}, _tabular_targets(content), "csv"

    return {}, _plain_targets(content), "txt"


def _normalize_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    mapped = _canonical_mapping(raw)
    result: dict[str, Any] = {}
    if "ssh_port" in mapped:
        result["ssh_port"] = _normalize_port(mapped.get("ssh_port"))
    if "environment" in mapped:
        result["environment"] = _normalize_environment(mapped.get("environment"))
    if "mode" in mapped:
        result["mode"] = _normalize_mode(mapped.get("mode"))
    if "objective" in mapped:
        result["objective"] = _clean_text(mapped.get("objective"))
    if "provider" in mapped:
        result["provider"] = _normalize_provider(mapped.get("provider"))
    if "model" in mapped:
        result["model"] = _clean_text(mapped.get("model"), limit=255)
    if "playbook_id" in mapped:
        result["playbook_id"] = _clean_text(mapped.get("playbook_id"), limit=255)
    if "playbook_mode" in mapped:
        result["playbook_mode"] = _normalize_playbook_mode(mapped.get("playbook_mode"))
    if result.get("playbook_id") and not result.get("playbook_mode"):
        result["playbook_mode"] = "manual"
    return {key: value for key, value in result.items() if value is not None}


def _normalize_target(raw: Any, defaults: dict[str, Any], index: int) -> dict[str, Any]:
    if isinstance(raw, str):
        mapped = _parse_target_token(raw)
    elif isinstance(raw, dict):
        mapped = _canonical_mapping(raw)
    else:
        raise BatchManifestError(f"alvo {index} precisa ser texto ou objeto")

    merged = {**defaults, **{key: value for key, value in mapped.items() if value not in (None, "")}}
    target = _clean_text(merged.get("target"), limit=255)
    hostname = _clean_text(merged.get("hostname"), limit=255)
    site = _clean_text(merged.get("site"), limit=255)
    target = target or hostname or site
    if not target:
        raise BatchManifestError(f"alvo {index} não possui IP, target, hostname ou site")
    if any(character in target for character in ("\n", "\r", "\x00")):
        raise BatchManifestError(f"alvo {index} contém caracteres inválidos")

    result: dict[str, Any] = {
        "target": target,
        "display_name": hostname or site or target,
    }
    if "ssh_port" in merged:
        result["ssh_port"] = _normalize_port(merged.get("ssh_port"))
    if "environment" in merged:
        result["environment"] = _normalize_environment(merged.get("environment"))
    if "mode" in merged:
        result["mode"] = _normalize_mode(merged.get("mode"))
    if "objective" in merged:
        result["objective"] = _clean_text(merged.get("objective"))
    if "provider" in merged:
        result["provider"] = _normalize_provider(merged.get("provider"))
    if "model" in merged:
        result["model"] = _clean_text(merged.get("model"), limit=255)
    if "playbook_id" in merged:
        result["playbook_id"] = _clean_text(merged.get("playbook_id"), limit=255)
    if "playbook_mode" in merged:
        result["playbook_mode"] = _normalize_playbook_mode(merged.get("playbook_mode"))
    if result.get("playbook_id") and not result.get("playbook_mode"):
        result["playbook_mode"] = "manual"
    return {key: value for key, value in result.items() if value is not None}


def parse_batch_manifest(
    filename: str,
    content: str,
    *,
    max_targets: int = 50,
) -> dict[str, Any]:
    if not str(content or "").strip():
        raise BatchManifestError("o arquivo está vazio")
    if max_targets < 1:
        raise BatchManifestError("limite de alvos inválido")

    raw_defaults, raw_targets, detected_format = _load_manifest(filename, content)
    defaults = _normalize_defaults(raw_defaults)
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[str, int | None]] = set()

    for index, raw in enumerate(raw_targets, start=1):
        item = _normalize_target(raw, defaults, index)
        key = (str(item["target"]).casefold(), item.get("ssh_port"))
        if key in seen:
            warnings.append(f"alvo duplicado ignorado: {item['target']}")
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) > max_targets:
            raise BatchManifestError(f"o lote excede o limite de {max_targets} alvos")

    if not normalized:
        raise BatchManifestError("nenhum alvo válido foi encontrado")

    return {
        "filename": Path(filename or "lote.txt").name,
        "format": detected_format,
        "total": len(normalized),
        "defaults": defaults,
        "items": normalized,
        "warnings": warnings,
    }
