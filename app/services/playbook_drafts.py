from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import Settings, get_settings
from app.services.persistence import (
    get_investigation,
    get_playbook_draft,
    review_playbook_draft,
    save_playbook_draft,
)
from app.services.playbooks import reload_playbooks


_MAX_DRAFT_BYTES = 100 * 1024
_STOPWORDS = {
    "alerta", "analisar", "ambiente", "causa", "checkmk", "cliente", "com", "como", "da", "das",
    "de", "do", "dos", "em", "estado", "falha", "identificar", "investigar", "no", "nos", "o", "os",
    "para", "pela", "pelo", "problema", "que", "serviço", "servico", "uma", "validar",
}


def _slug(value: str, limit: int = 70) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:limit].strip("-") or "incidente-validado"


def _patterns(objective: str) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_-]{3,}", objective)
    selected: list[str] = []
    for word in words:
        clean = word.casefold()
        if clean in _STOPWORDS or clean.isdigit() or clean in selected:
            continue
        selected.append(clean)
        if len(selected) >= 6:
            break
    return [rf"\b{re.escape(word)}\b" for word in selected] or [re.escape(objective[:80])]


def _read_only_steps(investigation: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for plan in investigation.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        for item in plan.get("tools") or plan.get("commands") or []:
            if not isinstance(item, dict) or not item.get("tool"):
                continue
            tool = str(item["tool"])
            key = f"{tool}:{item.get('arguments') or {}}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "tool": tool,
                    "arguments": dict(item.get("arguments") or {}),
                    "purpose": str(item.get("purpose") or "Coletar evidência relacionada ao sintoma."),
                }
            )
            if len(rows) >= 10:
                return rows
    return rows


def _validation_steps(investigation: dict[str, Any], correction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    successful_tools = {
        str(item.get("tool"))
        for item in investigation.get("evidence") or []
        if isinstance(item, dict) and item.get("tool") and int(item.get("exit_code") or 0) == 0
    }
    rows = [
        {"tool": tool, "arguments": {}, "purpose": "Confirmar o estado após a correção."}
        for tool in list(successful_tools)[:4]
    ]
    if rows:
        return rows
    return [
        {
            "tool": str(item.get("tool")),
            "arguments": dict(item.get("arguments") or {}),
            "purpose": "Revalidar a ferramenta corretiva somente após aprovação humana.",
        }
        for item in correction_results
        if item.get("tool") and item.get("status") == "validated"
    ][:3]


def generate_playbook_draft(
    investigation_id: str,
    correction_results: list[dict[str, Any]],
    *,
    generated_by: str | None = None,
) -> dict[str, Any] | None:
    validated = [item for item in correction_results if isinstance(item, dict) and item.get("status") == "validated"]
    if not validated:
        return None
    investigation = get_investigation(investigation_id, include_evidence=True)
    if not investigation:
        raise LookupError("investigação não encontrada")

    objective = str(investigation.get("objective") or "Incidente operacional validado").strip()
    analysis = dict(investigation.get("analysis") or {})
    profile = str(investigation.get("profile") or "any")
    playbook_id = f"learned-{_slug(objective)}-{investigation_id[:8]}"
    title = f"Solução validada: {objective[:120]}"
    payload = {
        "id": playbook_id,
        "title": title,
        "priority": 55,
        "profiles": [profile],
        "match": {"any": _patterns(objective)},
        "steps": _read_only_steps(investigation),
        "allowed_corrections": sorted({str(item.get("tool")) for item in validated if item.get("tool")}),
        "validation": _validation_steps(investigation, validated),
        "metadata": {
            "status": "draft",
            "source_investigation": investigation_id,
            "probable_cause": analysis.get("probable_cause"),
            "confidence": analysis.get("confidence"),
            "requires_human_review": True,
            "validated_actions": len(validated),
        },
    }
    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=110)
    if len(content.encode("utf-8")) > _MAX_DRAFT_BYTES:
        raise ValueError("o rascunho excedeu o limite de 100 KB")
    return save_playbook_draft(
        investigation_id,
        playbook_id=playbook_id,
        title=title,
        yaml_content=content,
        generated_by=generated_by,
        metadata=payload["metadata"],
    )


def activate_playbook_draft(
    draft_id: str,
    *,
    reviewed_by: str,
    review_notes: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    draft = get_playbook_draft(draft_id)
    if not draft:
        raise LookupError("rascunho de playbook não encontrado")
    if draft.get("status") == "approved" and draft.get("activated_path"):
        return draft

    content = str(draft.get("yaml_content") or "")
    if not content or len(content.encode("utf-8")) > _MAX_DRAFT_BYTES:
        raise ValueError("conteúdo YAML ausente ou acima de 100 KB")
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict) or parsed.get("id") != draft.get("playbook_id"):
        raise ValueError("o YAML do rascunho não corresponde ao playbook registrado")
    if parsed.get("metadata", {}).get("requires_human_review") is not True:
        raise ValueError("o rascunho não possui a marca obrigatória de revisão humana")

    directory = Path(settings.agent_playbook_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = (directory / f"{_slug(str(draft['playbook_id']), 100)}.yml").resolve()
    if directory not in destination.parents:
        raise ValueError("caminho de ativação do playbook é inválido")
    destination.write_text(content, encoding="utf-8")
    reload_playbooks()
    return review_playbook_draft(
        draft_id,
        status="approved",
        reviewed_by=reviewed_by,
        review_notes=review_notes,
        activated_path=str(destination),
    )


def reject_playbook_draft(
    draft_id: str,
    *,
    reviewed_by: str,
    review_notes: str | None = None,
) -> dict[str, Any]:
    return review_playbook_draft(
        draft_id,
        status="rejected",
        reviewed_by=reviewed_by,
        review_notes=review_notes,
    )
