from __future__ import annotations

import json
import re
from typing import Any

from app.core.settings import Settings, get_settings
from app.services.ai_providers import ProviderError, get_provider
from app.services.noc_skills import _skill_from_payload
from app.services.playbook_crud import read_playbook_document


_AUTO_SELECTIONS = {"", "auto", "automatic", "automatico", "automático", "default", "padrao", "padrão"}


def _automatic_as_none(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return None if normalized.casefold() in _AUTO_SELECTIONS else normalized


def _safe_id(value: str, fallback: str) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-_")
    if len(text) < 2:
        text = fallback
    return text[:64]


def _fallback_skill(playbook: dict[str, Any]) -> dict[str, Any]:
    playbook_id = str(playbook.get("id") or "playbook")
    knowledge: list[str] = []
    try:
        import yaml

        steps = yaml.safe_load(playbook.get("steps_yaml") or "[]") or []
    except Exception:
        steps = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        purpose = str(step.get("purpose") or step.get("tool") or "").strip()
        if purpose and purpose not in knowledge:
            knowledge.append(purpose[:1000])
    payload = {
        "id": _safe_id(f"{playbook_id}-skill", "playbook-skill"),
        "title": f"Skill · {str(playbook.get('title') or playbook_id)[:140]}",
        "priority": int(playbook.get("priority") or 0),
        "match": {
            "service": [],
            "output": [str(item) for item in playbook.get("patterns") or ()][:20],
            "host": [],
        },
        "target_strategy": "internal_ssh",
        "playbook_id": playbook_id,
        "objective": str(playbook.get("summary") or f"Investigar o cenário coberto pelo playbook {playbook_id}.")[:2000],
        "knowledge": knowledge[:20],
        "constraints": [str(item) for item in playbook.get("safety_rules") or ()][:20],
    }
    return _skill_from_payload(payload, source="playbook-fallback").as_dict()


def _prompt(playbook: dict[str, Any]) -> str:
    safe_document = {
        "id": playbook.get("id"),
        "title": playbook.get("title"),
        "priority": playbook.get("priority"),
        "profiles": playbook.get("profiles"),
        "patterns": playbook.get("patterns"),
        "summary": playbook.get("summary"),
        "required_inputs": playbook.get("required_inputs"),
        "safety_rules": playbook.get("safety_rules"),
        "validation_notes": playbook.get("validation_notes"),
        "steps_yaml": str(playbook.get("steps_yaml") or "")[:18000],
    }
    return f"""Você organiza especialistas (skills) de um NOC autônomo.
Um playbook é um conjunto de conhecimento e etapas; cada skill representa um problema operacional específico que deve reconhecer um alerta e encaminhá-lo ao playbook correto.

Analise o playbook abaixo e devolva JSON no formato:
{{"skills":[{{"id":"...","title":"...","priority":0,"match":{{"service":[],"output":[],"host":[]}},"target_strategy":"internal_ssh","playbook_id":"{playbook.get('id')}","objective":"...","knowledge":[],"constraints":[]}}]}}

Regras:
- Gere entre 1 e 8 skills, apenas quando houver cenários realmente distintos.
- Não invente clientes, IPs, hostnames ou nomes de sites.
- match.service deve conter regex/padrões de nomes de serviços quando o playbook trouxer evidência suficiente.
- match.output deve conter sintomas e mensagens relevantes, não comandos shell.
- match.host normalmente deve ficar vazio, a menos que o playbook seja explicitamente específico por tipo de host.
- playbook_id deve ser exatamente {playbook.get('id')} em todas as skills.
- target_strategy deve ser internal_ssh, exceto quando o próprio alerta já trouxer toda a evidência necessária.
- knowledge deve explicar como diagnosticar e interpretar o problema.
- constraints deve preservar as proteções e limites do playbook.
- Não inclua segredos, credenciais ou ações destrutivas.
- IDs: minúsculas, números, hífen ou underscore; 2 a 64 caracteres.
- Retorne somente JSON.

Playbook:
{json.dumps(safe_document, ensure_ascii=False)}
"""


def preview_skills_from_playbook(
    playbook_id: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    playbook = read_playbook_document(playbook_id, settings)
    selected_provider = _automatic_as_none(provider)
    selected_model = _automatic_as_none(model)
    ai_metadata: dict[str, Any] = {}

    try:
        ai = get_provider(selected_provider, settings=settings, model_name=selected_model)
        result, metadata = ai.generate_json(_prompt(playbook))
        raw_items = result.get("skills") if isinstance(result, dict) else None
        if not isinstance(raw_items, list):
            raw_items = []
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_items[:8], start=1):
            if not isinstance(raw, dict):
                continue
            payload = dict(raw)
            payload["id"] = _safe_id(payload.get("id"), f"{playbook_id}-skill-{index}")
            payload["playbook_id"] = playbook_id
            try:
                item = _skill_from_payload(payload, source="playbook-ai").as_dict()
            except (TypeError, ValueError):
                continue
            items.append(item)
        if items:
            ai_metadata = {
                "provider": getattr(ai, "name", selected_provider or "automático"),
                "model": getattr(ai, "model", selected_model or ""),
                **metadata,
            }
            return {"playbook": playbook, "items": items, "ai_metadata": ai_metadata, "fallback": False}
    except ProviderError:
        raise
    except Exception:
        pass

    return {"playbook": playbook, "items": [_fallback_skill(playbook)], "ai_metadata": ai_metadata, "fallback": True}
