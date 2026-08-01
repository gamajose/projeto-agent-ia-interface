from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HostORM(Base):
    __tablename__ = "hosts"
    __table_args__ = (UniqueConstraint("vpn_ip", "ssh_port", name="uq_host_vpn_port"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host_type: Mapped[str] = mapped_column(String(20), nullable=False)
    vpn_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    hostname: Mapped[str | None] = mapped_column(String(255))
    internal_ips: Mapped[list] = mapped_column(JSONB, default=list)
    os_name: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CustomerORM(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("key", name="uq_customer_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CustomerNodeORM(Base):
    __tablename__ = "customer_nodes"
    __table_args__ = (
        UniqueConstraint("customer_id", "address", "ssh_port", name="uq_customer_node_address_port"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inventory_host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hosts.id", ondelete="SET NULL"), index=True
    )
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    hostname: Mapped[str | None] = mapped_column(String(255))
    label: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    environment: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    direct_vpn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CustomerRouteORM(Base):
    __tablename__ = "customer_routes"
    __table_args__ = (
        UniqueConstraint("source_node_id", "destination_node_id", "route_type", name="uq_customer_route_nodes_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    route_type: Mapped[str] = mapped_column(String(30), nullable=False, default="ssh")
    username: Mapped[str | None] = mapped_column(String(255))
    credential_ref: Mapped[str] = mapped_column(String(120), nullable=False, default="SSH_DEFAULT_PASSWORD")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonitoringMappingORM(Base):
    __tablename__ = "monitoring_mappings"
    __table_args__ = (UniqueConstraint("affected_host_id", name="uq_mapping_affected_host"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    affected_host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    monitoring_host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    same_server: Mapped[bool] = mapped_column(Boolean, nullable=False)
    container_name: Mapped[str | None] = mapped_column(String(255))
    site_name: Mapped[str | None] = mapped_column(String(255))
    checkmk_hostname: Mapped[str | None] = mapped_column(String(255))
    checkmk_version: Mapped[str | None] = mapped_column(String(100))
    last_validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncidentORM(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    affected_host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    site_name: Mapped[str | None] = mapped_column(String(255))
    checkmk_host: Mapped[str] = mapped_column(String(255), nullable=False)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    normalized_output: Mapped[str | None] = mapped_column(Text)
    root_cause_status: Mapped[str] = mapped_column(String(30), nullable=False, default="inconclusive")
    root_cause: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentActionORM(Base):
    __tablename__ = "incident_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_code: Mapped[str] = mapped_column(String(80), nullable=False)
    approved_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    output_excerpt: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationORM(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="investigate")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="inconclusive")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plans: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    assessments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    diagnostics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ApprovalExecutionORM(Base):
    __tablename__ = "approval_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvestigationFeedbackORM(Base):
    __tablename__ = "investigation_feedback"
    __table_args__ = (
        UniqueConstraint("investigation_id", "operator", name="uq_investigation_feedback_operator"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    confirmed_cause: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlaybookDraftORM(Base):
    __tablename__ = "playbook_drafts"
    __table_args__ = (UniqueConstraint("investigation_id", name="uq_playbook_draft_investigation"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True)
    playbook_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    review_notes: Mapped[str | None] = mapped_column(Text)
    activated_path: Mapped[str | None] = mapped_column(String(1024))
    metadata_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
