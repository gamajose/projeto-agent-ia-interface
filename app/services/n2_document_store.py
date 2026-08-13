from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select

from app.db.base import SessionLocal, ensure_database_schema
from app.db.n2_models import N2DocumentORM
from app.services.n2_documentation import sanitize_n2_review


def _serialize(row: N2DocumentORM, *, include_review: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(row.id),
        "site_id": row.site_id,
        "client": row.client,
        "title": row.title,
        "status": row.status,
        "selected_hosts": row.selected_hosts or [],
        "responsibles": row.responsibles or {},
        "execution_ids": row.execution_ids or [],
        "last_export_format": row.last_export_format,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_review:
        payload["review"] = row.review_payload or {}
    return payload


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("identificador de documento N2 inválido") from exc


def save_n2_document(
    review: dict[str, Any],
    *,
    document_id: str | None = None,
    status: str = "collected",
    export_format: str | None = None,
    operator: str | None = None,
) -> dict[str, Any]:
    ensure_database_schema()
    safe = sanitize_n2_review(review)
    site_id = str(safe.get("site_id") or "").strip()
    client = str(safe.get("client") or site_id).strip()
    if not site_id:
        raise ValueError("site_id ausente na revisão N2")
    if not client:
        raise ValueError("cliente ausente na revisão N2")

    selected = safe.get("selected_hosts") if isinstance(safe.get("selected_hosts"), list) else []
    host_names = [str(item.get("host") or item.get("server") or "").strip() for item in selected if isinstance(item, dict)]
    host_names = [item for item in host_names if item]
    responsibles = safe.get("responsibles") if isinstance(safe.get("responsibles"), dict) else {}
    collection = safe.get("collection") if isinstance(safe.get("collection"), dict) else {}
    execution_ids = collection.get("completed_execution_ids") if isinstance(collection.get("completed_execution_ids"), list) else []
    title = str(safe.get("title") or f"Documentação N2 - {client}").strip()[:255]
    normalized_status = str(status or "collected").strip().lower()[:30] or "collected"
    normalized_format = str(export_format or "").strip().lower()[:12] or None

    with SessionLocal() as session:
        row: N2DocumentORM | None = None
        if document_id:
            row = session.get(N2DocumentORM, _uuid(document_id))
            if row is None:
                raise ValueError("documento N2 não encontrado")

        if row is None:
            row = N2DocumentORM(
                site_id=site_id,
                client=client,
                title=title,
                status=normalized_status,
                selected_hosts=host_names,
                responsibles=responsibles,
                execution_ids=execution_ids,
                review_payload=safe,
                last_export_format=normalized_format,
                created_by=str(operator or "").strip()[:255] or None,
            )
            session.add(row)
        else:
            row.site_id = site_id
            row.client = client
            row.title = title
            row.status = normalized_status
            row.selected_hosts = host_names
            row.responsibles = responsibles
            row.execution_ids = execution_ids
            row.review_payload = safe
            if normalized_format:
                row.last_export_format = normalized_format
            if operator and not row.created_by:
                row.created_by = str(operator).strip()[:255] or None

        session.commit()
        session.refresh(row)
        return _serialize(row, include_review=True)


def list_n2_documents(*, site_id: str | None = None, query: str | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_database_schema()
    safe_limit = max(1, min(int(limit or 100), 500))
    with SessionLocal() as session:
        statement = select(N2DocumentORM)
        if site_id:
            statement = statement.where(N2DocumentORM.site_id == str(site_id).strip())
        if query:
            term = f"%{str(query).strip()}%"
            statement = statement.where(
                or_(
                    N2DocumentORM.client.ilike(term),
                    N2DocumentORM.site_id.ilike(term),
                    N2DocumentORM.title.ilike(term),
                )
            )
        statement = statement.order_by(N2DocumentORM.updated_at.desc(), N2DocumentORM.created_at.desc()).limit(safe_limit)
        rows = session.scalars(statement).all()
        return {"items": [_serialize(row) for row in rows], "total": len(rows)}


def get_n2_document(document_id: str) -> dict[str, Any] | None:
    ensure_database_schema()
    with SessionLocal() as session:
        row = session.get(N2DocumentORM, _uuid(document_id))
        return _serialize(row, include_review=True) if row else None


def delete_n2_document(document_id: str) -> bool:
    ensure_database_schema()
    with SessionLocal() as session:
        row = session.get(N2DocumentORM, _uuid(document_id))
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
