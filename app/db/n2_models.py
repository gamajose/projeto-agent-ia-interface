from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class N2DocumentORM(Base):
    """Documento N2 revisável e reabrível pelo analista.

    O payload persistido já chega sanitizado pelo serviço N2. Credenciais,
    communities, tokens, secrets e chaves privadas nunca devem ser gravados
    nesta tabela.
    """

    __tablename__ = "n2_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    client: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="collected", index=True)
    selected_hosts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    responsibles: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    execution_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    review_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_export_format: Mapped[str | None] = mapped_column(String(12))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True
    )
