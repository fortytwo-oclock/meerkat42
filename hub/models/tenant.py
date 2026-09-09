from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hub.db.base import Base, TZDateTime, created_at_col, utcnow, uuid_pk

if TYPE_CHECKING:
    from hub.models.license import LicenseRecord
    from hub.models.node import ClientGroup


class Tenant(Base):
    """Top-level isolation unit.

    Self-hosted mode runs with exactly one tenant (`default`). Cloud creates one
    tenant per paying customer / MSP account.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    groups: Mapped[list["ClientGroup"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    licenses: Mapped[list["LicenseRecord"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
