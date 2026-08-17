from __future__ import annotations

from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session


_INSTALLED = False
_ADVISORY_LOCK_ID = 1475001


def _copy_fields(target: Any, source: Any, fields: tuple[str, ...]) -> None:
    for field in fields:
        setattr(target, field, getattr(source, field))


def _deduplicate_checkmk_new_rows(session: Session, _flush_context: object, _instances: object) -> None:
    """Consolida inserts Checkmk duplicados no mesmo flush e entre processos.

    ``agent-ia-web`` e ``agent-ia-worker`` são processos separados; portanto um
    ``threading.Lock`` no coletor não impede duas fotografias de persistirem ao
    mesmo tempo. Antes do flush das entidades Checkmk usamos um advisory lock
    transacional do PostgreSQL. Depois de obter o lock, consultamos novamente as
    chaves novas: se outro processo acabou de gravá-las, transformamos o INSERT
    concorrente em UPDATE da linha já existente.

    Isso também cobre a situação mais simples em que o próprio Livestatus devolve
    a mesma linha duas vezes dentro do mesmo payload.
    """

    from app.db.checkmk_master_models import CheckmkHostORM, CheckmkProblemORM

    checkmk_new = [obj for obj in list(session.new) if isinstance(obj, (CheckmkProblemORM, CheckmkHostORM))]
    if not checkmk_new:
        return

    get_bind = getattr(session, "get_bind", None)
    bind = get_bind() if callable(get_bind) else None
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "")
    if not dialect or dialect == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _ADVISORY_LOCK_ID})

    problems: dict[str, CheckmkProblemORM] = {}
    hosts: dict[tuple[str, str], CheckmkHostORM] = {}

    observation_fields = (
        "client_alias",
        "kind",
        "host_name",
        "internal_address",
        "service",
        "state",
        "state_name",
        "output",
        "active",
        "skill_id",
        "skill_title",
        "route_strategy",
        "metadata_payload",
        "last_seen_at",
        "resolved_at",
    )
    same_session_problem_fields = observation_fields + (
        "automation_status",
        "incident_id",
        "job_id",
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

    # Primeiro elimina duplicatas criadas dentro da mesma Session.
    for obj in list(session.new):
        if isinstance(obj, CheckmkProblemORM):
            key = str(obj.problem_key or "").strip()
            if not key:
                continue
            previous = problems.get(key)
            if previous is None:
                problems[key] = obj
                continue
            _copy_fields(previous, obj, same_session_problem_fields)
            previous.occurrence_count = max(int(previous.occurrence_count or 1), int(obj.occurrence_count or 1))
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

    # Depois do advisory lock, uma fotografia concorrente já pode ter commitado.
    # Atualizamos somente campos observacionais: estado da automação, incident_id
    # e job_id pertencem ao fluxo NOC e não podem voltar para "detected" por causa
    # de uma segunda fotografia concorrente.
    for key, obj in list(problems.items()):
        existing = session.scalar(select(CheckmkProblemORM).where(CheckmkProblemORM.problem_key == key))
        if existing is None or existing is obj:
            continue
        _copy_fields(existing, obj, observation_fields)
        existing.occurrence_count = int(existing.occurrence_count or 0) + max(1, int(obj.occurrence_count or 1))
        session.expunge(obj)
        problems[key] = existing

    for (site_id, host_name), obj in list(hosts.items()):
        existing = session.scalar(
            select(CheckmkHostORM).where(
                CheckmkHostORM.site_id == site_id,
                CheckmkHostORM.host_name == host_name,
            )
        )
        if existing is None or existing is obj:
            continue
        _copy_fields(existing, obj, host_fields)
        session.expunge(obj)
        hosts[(site_id, host_name)] = existing


def install_checkmk_session_guards() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _deduplicate_checkmk_new_rows)
    _INSTALLED = True
