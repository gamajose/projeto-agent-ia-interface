from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FleetDiscoveryRunORM(Base):
    __tablename__ = "fleet_discovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trigger: Mapped[str] = mapped_column(String(30), nullable=False, default="initial")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    cidrs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    cursor_cidr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accessible: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inaccessible: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    monitoring_detected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FleetAssetORM(Base):
    __tablename__ = "fleet_assets"
    __table_args__ = (UniqueConstraint("address", "ssh_port", name="uq_fleet_asset_address_port"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fleet_discovery_runs.id", ondelete="SET NULL"), index=True
    )
    inventory_host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hosts.id", ondelete="SET NULL"), index=True
    )
    address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    client_name: Mapped[str | None] = mapped_column(String(255), index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), index=True)
    os_name: Mapped[str | None] = mapped_column(String(255))
    access_status: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown", index=True)
    environment: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown", index=True)
    roles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    monitoring_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    monitoring_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkmk_sites: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_accessible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
