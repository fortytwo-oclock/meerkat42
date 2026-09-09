from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime, created_at_col, uuid_pk


class Role(str, enum.Enum):
    OWNER = "owner"      # full control incl. token management
    ADMIN = "admin"      # all tenant operations
    VIEWER = "viewer"    # read-only, optionally scoped to one client group


# ordering for require_role() checks
ROLE_RANK = {Role.VIEWER: 0, Role.ADMIN: 1, Role.OWNER: 2}


class ApiToken(Base):
    """A bearer credential. The raw secret is shown once on creation; only its
    SHA-256 hash is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[Role] = mapped_column(String(16), default=Role.VIEWER)
    # non-null only for VIEWER tokens scoped to a single client group
    group_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("client_groups.id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[str] = mapped_column(String(200), default="owner")
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)


class AgentEnrollmentToken(Base):
    """Tenant/group-bound agent credential; only a SHA-256 digest is stored."""

    __tablename__ = "agent_enrollment_tokens"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(
        ForeignKey("client_groups.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[str] = mapped_column(String(200), default="owner")
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)


class EmergencyAccessGrant(Base):
    """Short-lived break-glass credential; only its SHA-256 digest is stored."""

    __tablename__ = "emergency_access_grants"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
