from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session


_INSTALLED = False


def _copy_fields(target: Any, source: Any, fields: tuple[str, ...]) -> None:
    for field in fields:
        setattr(target, field, getattr(source, field))


def _deduplicate_checkmk_new_rows(session: Session, _flush_context: object, _instances: object) -> None:
    """Evita INSERT duplicado quando o Livestatus devolve a mesma linha mais de uma vez.

    O coletor trabalha com ``autoflush=False``. Se um problema ainda não existe no
    banco e aparece duas vezes no mesmo payload, as duas instâncias ficam em
    ``session.new`` até o commit. A constraint de ``problem_key`` então rejeita o
    segundo INSERT. Consolidamos essas instâncias antes do flush, preservando a
    versão mais recente dos campos recebidos.
    """

    from app.db.checkmk_master_models import CheckmkHostORM, CheckmkProblemORM

    problems: dict[str, CheckmkProblemORM] = {}
    hosts: dict[tuple[str, str], CheckmkHostORM] = {}

    problem_fields = (
        "client_alias",
        "kind",
        "host_name",
        "internal_address",
        "service",
        "state",
        "state_name",
        "output",
        "active",
        "occurrence_count",
        "skill_id",
        "skill_title",
        "route_strategy",
        "automation_status",
        "incident_id",
        "job_id",
        "metadata_payload",
        "last_seen_at",
        "resolved_at",
    )
    host_fields = (
        "client_alias",
        "internal_address",
        "state",
        "environment",
        "host_kind",
        "ssh_port",
        "metadata_payload",
        "last_seen_at",
    )

    for obj in list(session.new):
        if isinstance(obj, CheckmkProblemORM):
            key = str(obj.problem_key or "").strip()
            if not key:
                continue
            previous = problems.get(key)
            if previous is None:
                problems[key] = obj
                continue
            _copy_fields(previous, obj, problem_fields)
            previous.occurrence_count = max(
                int(previous.occurrence_count or 1),
                int(obj.occurrence_count or 1),
            )
            session.expunge(obj)
            continue

        if isinstance(obj, CheckmkHostORM):
            key = (str(obj.site_id or "").strip(), str(obj.host_name or "").strip())
            if not all(key):
                continue
            previous = hosts.get(key)
            if previous is None:
                hosts[key] = obj
                continue
            _copy_fields(previous, obj, host_fields)
            session.expunge(obj)


def install_checkmk_session_guards() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _deduplicate_checkmk_new_rows)
    _INSTALLED = True
