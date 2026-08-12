from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import PROJECT_ROOT, get_settings
from app.services.runtime_env import runtime_value


@dataclass(frozen=True)
class NOCSkill:
    id: str
    title: str
    priority: int
    service_patterns: tuple[str, ...]
    output_patterns: tuple[str, ...]
    host_patterns: tuple[str, ...]
    target_strategy: str
    playbook_id: str | None
    objective: str
    knowledge: tuple[str, ...]
    constraints: tuple[str, ...]
    source: str

    @staticmethod
    def _matches(patterns: tuple[str, ...], value: str) -> int:
        hits = 0
        for pattern in patterns:
            try:
                if re.search(pattern, value, flags=re.IGNORECASE):
                    hits += 1
            except re.error:
                if pattern.casefold() in value.casefold():
                    hits += 1
        return hits

    def score(self, event: dict[str, Any]) -> int:
        service = str(event.get("service") or "")
        output = str(event.get("output") or "")
        host = str(event.get("host") or "")
        service_hits = self._matches(self.service_patterns, service)
        output_hits = self._matches(self.output_patterns, output)
        host_hits = self._matches(self.host_patterns, host)
        if not any((service_hits, output_hits, host_hits)):
            return -1
        return self.priority + service_hits * 40 + output_hits * 25 + host_hits * 30

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "priority": self.priority,
            "target_strategy": self.target_strategy,
            "playbook_id": self.playbook_id,
            "knowledge": list(self.knowledge),
            "constraints": list(self.constraints),
            "source": self.source,
        }


def _skill_dir() -> Path:
    settings = get_settings()
    configured = str(runtime_value("NOC_SKILL_DIR", "", settings=settings) or "").strip()
    return Path(configured).expanduser() if configured else PROJECT_ROOT / "config" / "skills"


@lru_cache(maxsize=1)
def load_noc_skills() -> tuple[NOCSkill, ...]:
    directory = _skill_dir()
    if not directory.exists():
        return ()
    result: list[NOCSkill] = []
    for path in sorted(directory.glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        match = dict(payload.get("match") or {})
        result.append(
            NOCSkill(
                id=str(payload.get("id") or path.stem),
                title=str(payload.get("title") or path.stem),
                priority=int(payload.get("priority") or 0),
                service_patterns=tuple(str(item) for item in match.get("service") or ()),
                output_patterns=tuple(str(item) for item in match.get("output") or ()),
                host_patterns=tuple(str(item) for item in match.get("host") or ()),
                target_strategy=str(payload.get("target_strategy") or "internal_ssh").strip().lower(),
                playbook_id=str(payload.get("playbook_id") or "").strip() or None,
                objective=str(payload.get("objective") or "Investigar a causa do alerta usando somente evidências verificáveis."),
                knowledge=tuple(str(item) for item in payload.get("knowledge") or ()),
                constraints=tuple(str(item) for item in payload.get("constraints") or ()),
                source=str(path),
            )
        )
    return tuple(result)


def reload_noc_skills() -> tuple[NOCSkill, ...]:
    load_noc_skills.cache_clear()
    return load_noc_skills()


def select_noc_skill(event: dict[str, Any], *, host_kind: str | None = None) -> dict[str, Any]:
    ranked = sorted(
        ((skill.score(event), skill) for skill in load_noc_skills()),
        key=lambda item: item[0],
        reverse=True,
    )
    selected = ranked[0][1] if ranked and ranked[0][0] >= 0 else None
    if selected:
        payload = selected.as_dict()
    else:
        payload = {
            "id": "generic-checkmk-alert",
            "title": "Investigação genérica de alerta Checkmk",
            "priority": 0,
            "target_strategy": "internal_ssh",
            "playbook_id": None,
            "knowledge": [
                "Tratar o alerta como sintoma e confirmar a causa com dados do host.",
                "Usar o output do Checkmk como ponto de partida, nunca como prova única.",
            ],
            "constraints": ["Somente leitura até a política de correção autorizar uma ação."],
            "source": "builtin",
        }

    kind = str(host_kind or event.get("host_kind") or "server").casefold()
    address = str(event.get("host_address") or "").strip()
    if kind in {"bmc", "firewall", "network"}:
        payload["target_strategy"] = "entry_context"
    if address in {"", "0.0.0.0", "127.0.0.1", "::1"} or kind == "monitoring_local":
        payload["target_strategy"] = "entry_context"
    return payload


def build_skill_objective(
    event: dict[str, Any],
    skill: dict[str, Any],
    *,
    site_id: str,
    client_alias: str,
) -> str:
    state = str(event.get("state_name") or event.get("state") or "ALERT")
    lines = [
        f"Skill operacional: {skill.get('title') or skill.get('id')}",
        f"Cliente/site isolado: {client_alias} ({site_id})",
        f"Host Checkmk: {event.get('host') or '-'}",
        f"IP interno: {event.get('host_address') or '-'}",
        f"Servico: {event.get('service') or '-'}",
        f"Estado: {state}",
        f"Output Checkmk: {event.get('output') or '-'}",
        "Objetivo: investigar a causa real do alerta e validar o proximo passo seguro.",
    ]
    knowledge = [str(item) for item in skill.get("knowledge") or []]
    constraints = [str(item) for item in skill.get("constraints") or []]
    if knowledge:
        lines.append("Conhecimento da skill: " + " | ".join(knowledge))
    if constraints:
        lines.append("Restricoes: " + " | ".join(constraints))
    lines.append(
        "Regra de isolamento: nao usar IP interno, rota, sessao ou evidencia pertencente a outro site/cliente."
    )
    return "\n".join(lines)
