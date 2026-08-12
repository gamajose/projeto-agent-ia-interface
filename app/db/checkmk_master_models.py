from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CheckmkSiteORM(Base):
    """Site remoto conhecido pelo Checkmk master.

    ``site_id`` e o limite de isolamento operacional. Enderecos internos so sao
    validos quando combinados com esse site; o mesmo 192.168.x.x pode existir
    em clientes diferentes sem representar o mesmo equipamento.
    """

    __tablename__ = "checkmk_master_sites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    replication: Mapped[str | None] = mapped_column(String(40))
    livestatus_host: Mapped[str | None] = mapped_column(String(64), index=True)
    livestatus_port: Mapped[int | None] = mapped_column(Integer)
    status_site: Mapped[str | None] = mapped_column(String(64))
    status_host: Mapped[str | None] = mapped_column(String(255), index=True)
    multisite_url: Mapped[str | None] = mapped_column(String(1024))
    shared_endpoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    host_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    problem_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_problem_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    metadata_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CheckmkHostORM(Base):
    """Host interno pertencente a um unico site/cliente Checkmk."""

    __tablename__ = "checkmk_master_hosts"
    __table_args__ = (
        UniqueConstraint("site_id", "host_name", name="uq_checkmk_master_host_site_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    client_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    host_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    internal_address: Mapped[str | None] = mapped_column(String(64), index=True)
    state: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    environment: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown", index=True)
    host_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="server", index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    metadata_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
