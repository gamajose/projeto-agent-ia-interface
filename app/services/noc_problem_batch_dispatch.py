from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.settings import Settings, get_settings
from app.services import noc_problem_batch as batch
from app.services.noc_autonomy_control import request_selected_run
from app.services.noc_skills import load_noc_skills, select_noc_skill


_RECENT_SNAPSHOT_MAX_AGE_SECONDS = 180.0


def _parsed_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _all_recent(items: list[dict[str, Any]]) -> tuple[bool, str | None]:
    if not items:
        return False, None
    now = datetime.now(timezone.utc)
    timestamps: list[datetime] = []
    for item in items:
        parsed = _parsed_time(item.get("last_seen_at"))
        if parsed is None:
            return False, None
        age = (now - parsed).total_seconds()
        if age < 0 or age > _RECENT_SNAPSHOT_MAX_AGE_SECONDS:
            return False, None
        timestamps.append(parsed)
    return True, max(timestamps).isoformat() if timestamps else None


def request_procedure_batch(
    procedure_id: str,
    *,
    sites: list[str] | None = None,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Enfileira o lote sem repetir uma fotografia global quando a ronda é recente.

    O worker já mantém o PostgreSQL atualizado. Se todos os alertas do procedure
    foram vistos nos últimos três minutos, esse estado é usado como handoff para
    a fila. Se algum item estiver antigo, caímos automaticamente no caminho
    conservador original, que força uma nova fotografia do Checkmk.
    """

    settings = settings or get_settings()
    normalized = str(procedure_id or "").strip()
    known = {skill.id: skill for skill in load_noc_skills()}
    if normalized not in known:
        raise ValueError(f"procedure inexistente na NOC Master Skill: {normalized}")

    active = batch._active_batch_run(normalized, settings=settings)
    if active:
        metadata = dict(active.get("batch") or {})
        metadata["reused"] = True
        return {**active, "batch": metadata}

    site_filter = {str(item).strip() for item in sites or [] if str(item).strip()}
    matched: list[dict[str, Any]] = []
    for raw in batch._persisted_active_problems():
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if site_filter and batch._site_id(item) not in site_filter:
            continue
        selected = select_noc_skill(batch._event(item), host_kind=str(item.get("host_kind") or "") or None)
        selected_id = str(selected.get("procedure_id") or selected.get("id") or "")
        if selected_id == normalized and batch._problem_key(item):
            matched.append(item)

    recent, snapshot_completed_at = _all_recent(matched)
    if not recent:
        return batch.request_procedure_batch(
            normalized,
            sites=sorted(site_filter) if site_filter else None,
            operator=operator,
            settings=settings,
        )

    problem_keys = list(dict.fromkeys(batch._problem_key(item) for item in matched if batch._problem_key(item)))
    run = request_selected_run(
        sites=sorted(site_filter) if site_filter else None,
        problem_keys=problem_keys,
        skill_id=normalized,
        operator=operator,
        settings=settings,
    )
    skill = known[normalized]
    metadata = {
        "master_skill_id": "noc-master",
        "procedure_id": normalized,
        "title": skill.title,
        "problem_count": len(problem_keys),
        "host_count": len({batch._host(item) for item in matched if batch._host(item)}),
        "site_count": len({batch._site_id(item) for item in matched if batch._site_id(item)}),
        "problem_keys": problem_keys,
        "snapshot_source": "recent_persisted",
        "snapshot_completed_at": snapshot_completed_at,
        "reused": False,
        "snapshot_max_age_seconds": int(_RECENT_SNAPSHOT_MAX_AGE_SECONDS),
    }
    return batch._save_batch_context(
        run,
        procedure_id=normalized,
        batch=metadata,
        snapshot_completed_at=snapshot_completed_at,
        settings=settings,
    )
