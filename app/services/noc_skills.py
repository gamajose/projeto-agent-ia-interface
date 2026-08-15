from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import PROJECT_ROOT, get_settings
from app.services.runtime_env import runtime_value


_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_MASTER_SKILL_FILENAME = "noc-master.yml"
_MASTER_SKILL_ID = "noc-master"


@dataclass(frozen=True)
class NOCSkill:
    """Procedure interno da NOC Master Skill.

    O nome da classe é mantido por compatibilidade com o restante da aplicação,
    porém o catálogo físico agora possui uma única Skill mestre. Cada instância
    representa apenas um procedure selecionável internamente pelo tipo do alerta.
    """

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
    prescribed_actions: tuple[dict[str, Any], ...]
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
            "match": {
                "service": list(self.service_patterns),
                "output": list(self.output_patterns),
                "host": list(self.host_patterns),
            },
            "target_strategy": self.target_strategy,
            "playbook_id": self.playbook_id,
            "objective": self.objective,
            "knowledge": list(self.knowledge),
            "constraints": list(self.constraints),
            "prescribed_actions": [dict(item) for item in self.prescribed_actions],
            "source": self.source,
            "master_skill_id": _MASTER_SKILL_ID,
            "procedure_id": self.id,
            "editable": True,
        }


def _skill_dir() -> Path:
    settings = get_settings()
    configured = str(runtime_value("NOC_SKILL_DIR", "", settings=settings) or "").strip()
    return Path(configured).expanduser() if configured else PROJECT_ROOT / "config" / "skills"


def _master_skill_path() -> Path:
    return _skill_dir() / _MASTER_SKILL_FILENAME


def _runtime_catalog_path() -> Path:
    settings = get_settings()
    configured = str(runtime_value("NOC_SKILL_CATALOG_PATH", "", settings=settings) or "").strip()
    if configured:
        return Path(configured).expanduser()
    install_root = str(runtime_value("AGENT_INSTALL_ROOT", "", settings=settings) or "").strip()
    root = Path(install_root).expanduser() if install_root else PROJECT_ROOT.parent
    return root / "data" / "noc-skills.yml"


def _normalize_id(value: Any) -> str:
    skill_id = str(value or "").strip().lower()
    if not _SKILL_ID_RE.fullmatch(skill_id):
        raise ValueError("id da skill deve usar apenas letras minúsculas, números, hífen ou underscore")
    return skill_id


def _skill_from_payload(payload: dict[str, Any], *, source: str, fallback_id: str = "") -> NOCSkill:
    match = dict(payload.get("match") or {})
    skill_id = _normalize_id(payload.get("id") or fallback_id)
    title = str(payload.get("title") or skill_id).strip()[:160] or skill_id
    strategy = str(payload.get("target_strategy") or "internal_ssh").strip().lower()
    if strategy not in {"internal_ssh", "entry_context"}:
        raise ValueError("target_strategy deve ser internal_ssh ou entry_context")
    priority = int(payload.get("priority") or 0)
    if priority < -1000 or priority > 10000:
        raise ValueError("priority fora do intervalo permitido")
    prescribed_actions = tuple(
        dict(item)
        for item in payload.get("prescribed_actions") or ()
        if isinstance(item, dict)
    )
    return NOCSkill(
        id=skill_id,
        title=title,
        priority=priority,
        service_patterns=tuple(str(item)[:500] for item in match.get("service") or ()),
        output_patterns=tuple(str(item)[:500] for item in match.get("output") or ()),
        host_patterns=tuple(str(item)[:500] for item in match.get("host") or ()),
        target_strategy=strategy,
        playbook_id=str(payload.get("playbook_id") or "").strip()[:120] or None,
        objective=str(payload.get("objective") or "Investigar a causa do alerta usando somente evidências verificáveis.").strip()[:2000],
        knowledge=tuple(str(item)[:1000] for item in payload.get("knowledge") or ()),
        constraints=tuple(str(item)[:1000] for item in payload.get("constraints") or ()),
        prescribed_actions=prescribed_actions,
        source=source,
    )


def _read_runtime_catalog() -> dict[str, Any]:
    path = _runtime_catalog_path()
    if not path.exists():
        return {"items": {}, "disabled": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = payload.get("items") or {}
    disabled = payload.get("disabled") or []
    if not isinstance(items, dict):
        items = {}
    if not isinstance(disabled, list):
        disabled = []
    return {"items": items, "disabled": [str(item) for item in disabled]}


def _write_runtime_catalog(payload: dict[str, Any]) -> None:
    path = _runtime_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".noc-skills.", suffix=".yml", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _builtin_procedures() -> dict[str, NOCSkill]:
    """Lê uma única Skill física e expõe seus procedures internamente.

    Durante upgrades antigos sem ``noc-master.yml`` ainda aceitamos o formato
    legado de vários arquivos para não quebrar uma instalação no meio do deploy.
    Assim que a Skill mestre existe, nenhum outro YAML do diretório participa do
    ranking e não há conhecimento concorrente.
    """

    directory = _skill_dir()
    master = directory / _MASTER_SKILL_FILENAME
    result: dict[str, NOCSkill] = {}
    if master.exists():
        payload = yaml.safe_load(master.read_text(encoding="utf-8")) or {}
        if str(payload.get("id") or "").strip() != _MASTER_SKILL_ID:
            raise ValueError(f"{master}: id deve ser {_MASTER_SKILL_ID}")
        procedures = payload.get("procedures") or []
        if not isinstance(procedures, list) or not procedures:
            raise ValueError(f"{master}: procedures deve conter ao menos um procedimento")
        for index, raw in enumerate(procedures):
            if not isinstance(raw, dict):
                raise ValueError(f"{master}: procedure {index + 1} inválido")
            skill = _skill_from_payload(
                dict(raw),
                source=f"{master}#procedures[{index}]",
            )
            if skill.id in result:
                raise ValueError(f"{master}: procedure duplicado: {skill.id}")
            result[skill.id] = skill
        return result

    if directory.exists():
        for path in sorted(directory.glob("*.yml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            skill = _skill_from_payload(payload, source=str(path), fallback_id=path.stem)
            result[skill.id] = skill
    return result


@lru_cache(maxsize=1)
def load_noc_skills() -> tuple[NOCSkill, ...]:
    result = _builtin_procedures()

    # Overrides runtime continuam sendo armazenados em um único catálogo de
    # dados. Eles substituem procedures por ID, sem criar novas Skills físicas.
    runtime = _read_runtime_catalog()
    disabled = {str(item).strip().lower() for item in runtime.get("disabled") or []}
    for skill_id in disabled:
        result.pop(skill_id, None)
    for raw_id, raw_payload in (runtime.get("items") or {}).items():
        payload = dict(raw_payload or {})
        payload["id"] = raw_id
        skill = _skill_from_payload(payload, source=str(_runtime_catalog_path()))
        result[skill.id] = skill
    return tuple(sorted(result.values(), key=lambda item: (-item.priority, item.id)))


def reload_noc_skills() -> tuple[NOCSkill, ...]:
    load_noc_skills.cache_clear()
    return load_noc_skills()


def noc_master_skill() -> dict[str, Any]:
    """Retorna a única Skill apresentada à interface e seus procedures."""

    path = _master_skill_path()
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        title = str(payload.get("title") or "NOC Master Skill")
        objective = str(payload.get("objective") or "")
        principles = [str(item) for item in payload.get("principles") or []]
    else:
        title = "NOC Master Skill"
        objective = "Catálogo operacional central do NOC."
        principles = []
    procedures = [skill.as_dict() for skill in load_noc_skills()]
    return {
        "id": _MASTER_SKILL_ID,
        "title": title,
        "objective": objective,
        "principles": principles,
        "procedures": procedures,
        "procedure_count": len(procedures),
        "source": str(path),
        "editable": False,
    }


def save_noc_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Salva override de um procedure interno no catálogo runtime único."""

    skill = _skill_from_payload(payload, source=str(_runtime_catalog_path()))
    catalog = _read_runtime_catalog()
    items = dict(catalog.get("items") or {})
    items[skill.id] = {
        "title": skill.title,
        "priority": skill.priority,
        "match": {
            "service": list(skill.service_patterns),
            "output": list(skill.output_patterns),
            "host": list(skill.host_patterns),
        },
        "target_strategy": skill.target_strategy,
        "playbook_id": skill.playbook_id,
        "objective": skill.objective,
        "knowledge": list(skill.knowledge),
        "constraints": list(skill.constraints),
        "prescribed_actions": [dict(item) for item in skill.prescribed_actions],
    }
    disabled = [item for item in catalog.get("disabled") or [] if str(item).strip().lower() != skill.id]
    _write_runtime_catalog({"items": items, "disabled": disabled})
    reload_noc_skills()
    return skill.as_dict()


def delete_noc_skill(skill_id: str) -> dict[str, Any]:
    normalized = _normalize_id(skill_id)
    catalog = _read_runtime_catalog()
    items = dict(catalog.get("items") or {})
    existed_custom = normalized in items
    items.pop(normalized, None)
    disabled = {str(item).strip().lower() for item in catalog.get("disabled") or []}
    disabled.add(normalized)
    _write_runtime_catalog({"items": items, "disabled": sorted(disabled)})
    reload_noc_skills()
    return {"id": normalized, "removed": True, "custom_override_removed": existed_custom}


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
            "prescribed_actions": [],
            "source": "builtin",
            "master_skill_id": _MASTER_SKILL_ID,
            "procedure_id": "generic-checkmk-alert",
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
        "Skill operacional: NOC Master Skill",
        f"Procedure selecionado: {skill.get('title') or skill.get('id')}",
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
    prescribed = [dict(item) for item in skill.get("prescribed_actions") or [] if isinstance(item, dict)]
    if knowledge:
        lines.append("Conhecimento do procedure: " + " | ".join(knowledge))
    if constraints:
        lines.append("Restricoes: " + " | ".join(constraints))
    if prescribed:
        lines.append(
            "Acoes prescritas pelo procedure: "
            + yaml.safe_dump(prescribed, allow_unicode=True, sort_keys=False, default_flow_style=True).strip()
        )
        lines.append(
            "Regra de precedencia: estas acoes prescritas devem ser executadas pelo canal estruturado da Skill; "
            "nao trate um veto do Ansible ou da politica generica como substituto da prescricao."
        )
    lines.append(
        "Regra de isolamento: nao usar IP interno, rota, sessao ou evidencia pertencente a outro site/cliente."
    )
    return "\n".join(lines)
