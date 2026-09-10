"""Shared FastAPI dependencies, auth/RBAC, and app-scoped singletons."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import get_session
from hub.license_manager import LicenseManager
from hub.models.auth import ROLE_RANK, ApiToken, Role
from hub.timeseries import MetricStore


def get_license_manager(request: Request) -> LicenseManager:
    """Default-tenant manager for the public, pre-auth branding endpoint."""
    return request.app.state.license_manager


def get_metric_store(request: Request) -> MetricStore:
    return request.app.state.metric_store


@dataclass(frozen=True)
class Principal:
    role: Role
    tenant_id: str
    group_id: str | None
    label: str  # for audit fields / logging

    def can(self, minimum: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _bearer(authorization: str | None, x_admin_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_admin_token


async def authenticate(
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
    x_tenant_slug: str | None = Header(default=None),
) -> Principal:
    presented = _bearer(authorization, x_admin_token)
    return await authenticate_presented(
        session,
        presented=presented,
        default_tenant_id=request.app.state.default_tenant_id,
        tenant_slug=x_tenant_slug,
    )


async def authenticate_presented(
    session: AsyncSession,
    *,
    presented: str | None,
    default_tenant_id: str,
    tenant_slug: str | None = None,
) -> Principal:
    """Authenticate a raw credential for HTTP or WebSocket transports."""
    settings = get_settings()
    tenant_id = default_tenant_id

    # 1. Root ADMIN_TOKEN -> owner
    if settings.admin_token and presented and hmac.compare_digest(
        presented, settings.admin_token
    ):
        if tenant_slug:
            from hub.models import Tenant

            row = await session.execute(
                select(Tenant.id).where(Tenant.slug == tenant_slug.strip().lower())
            )
            selected = row.scalar_one_or_none()
            if selected is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            tenant_id = selected
        return Principal(Role.OWNER, tenant_id, None, "admin-token")

    # 2. Issued API token
    if presented:
        row = await session.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash(presented),
                ApiToken.revoked_at.is_(None),
            )
        )
        tok = row.scalar_one_or_none()
        if tok is not None:
            # Touch last_used_at in an isolated transaction so it doesn't couple
            # to whatever the route does with its own session.
            from hub.db.session import session_scope

            async with session_scope() as touch:
                await touch.execute(
                    ApiToken.__table__.update()
                    .where(ApiToken.id == tok.id)
                    .values(last_used_at=datetime.now(timezone.utc))
                )
            return Principal(
                Role(tok.role) if not isinstance(tok.role, Role) else tok.role,
                tok.tenant_id,
                tok.group_id,
                tok.name,
            )
        raise HTTPException(status_code=401, detail="invalid or revoked token")

    # 3. No credential presented. Open mode only if nothing is configured.
    tokens_exist = (
        await session.execute(select(func.count()).select_from(ApiToken))
    ).scalar_one()
    if not settings.admin_token and not tokens_exist:
        return Principal(Role.OWNER, tenant_id, None, "anonymous")

    raise HTTPException(status_code=401, detail="authentication required")


async def get_tenant_id(
    principal: Principal = Depends(authenticate),
) -> str:
    """Resolve tenant scope from the authenticated principal."""
    return principal.tenant_id


async def get_tenant_license_manager(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
) -> LicenseManager:
    """Load and cryptographically re-verify the authenticated tenant's license."""
    from hub.repositories import licenses as license_repo

    record = await license_repo.get_active(session, tenant_id)
    manager = LicenseManager()
    manager.load_from_record(record.raw_token if record else None, {})
    return manager


def require_role(minimum: Role):
    async def _dep(principal: Principal = Depends(authenticate)) -> Principal:
        if not principal.can(minimum):
            raise HTTPException(
                status_code=403, detail=f"requires {minimum.value} role"
            )
        return principal

    return _dep


# Back-compat: mutating endpoints depend on this. Returns the principal (its
# `.label` is used as the audit actor); anything below admin gets 403.
require_admin = require_role(Role.ADMIN)
require_owner = require_role(Role.OWNER)


async def require_configured_owner(
    principal: Principal = Depends(require_owner),
) -> Principal:
    """Owner access that refuses anonymous single-operator mode.

    Billing credentials and generated license tokens must never become public
    merely because a self-hosted instance has not configured authentication.
    """
    if principal.label == "anonymous":
        raise HTTPException(
            status_code=403,
            detail="this endpoint requires configured owner authentication",
        )
    return principal


async def viewer_scope(
    principal: Principal = Depends(authenticate),
    session: AsyncSession = Depends(get_session),
) -> str | None:
    """Group slug a viewer is locked to, or None for unscoped principals."""
    if principal.group_id is None:
        return None
    from hub.models import ClientGroup

    row = await session.execute(
        select(ClientGroup.slug).where(ClientGroup.id == principal.group_id)
    )
    return row.scalar_one_or_none()
