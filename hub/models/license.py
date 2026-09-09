from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hub.db.base import Base, TZDateTime, created_at_col, uuid_pk

if TYPE_CHECKING:
    from hub.models.tenant import Tenant


class LicenseRecord(Base):
    """A historical record of every applied license.

    Exactly one row per tenant has `is_active=True`. The raw token is stored so
    the Ed25519 signature can be *re-verified* on load — the DB is never trusted
    as the source of truth for claims.
    """

    __tablename__ = "license_records"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    raw_token: Mapped[Optional[str]] = mapped_column(Text, default=None)  # None = community default
    tier: Mapped[str] = mapped_column(String(32), default="community")
    customer_name: Mapped[str] = mapped_column(String(200), default="Community Edition User")
    issuer: Mapped[Optional[str]] = mapped_column(String(200), default=None)
    subject: Mapped[Optional[str]] = mapped_column(String(200), default=None)
    max_nodes: Mapped[int] = mapped_column(default=3)  # -1 = unlimited
    features: Mapped[list] = mapped_column(default=list)
    white_label_logo: Mapped[Optional[str]] = mapped_column(String(500), default=None)

    issued_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)

    is_active: Mapped[bool] = mapped_column(default=False, index=True)
    applied_at: Mapped[datetime] = created_at_col()

    tenant: Mapped["Tenant"] = relationship(back_populates="licenses")
