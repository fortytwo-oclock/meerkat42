from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime, created_at_col, uuid_pk


class RemoteCommand(Base):
    __tablename__ = "remote_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_command_tenant_idempotency"),
    )

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    requested_by: Mapped[str] = mapped_column(String(200))
    destructive: Mapped[bool] = mapped_column(default=False)
    approval_required: Mapped[bool] = mapped_column(default=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(200), default=None)
    approved_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None, index=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    timeout_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    result: Mapped[Optional[dict]] = mapped_column(default=None)
    error: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = created_at_col()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(255), index=True)
    details: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = created_at_col()
