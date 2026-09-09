from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.deps import token_hash
from hub.models import EmergencyAccessGrant


async def create(
    session: AsyncSession, *, tenant_id: str, actor: str, ttl_seconds: int
) -> tuple[EmergencyAccessGrant, str]:
    raw = "cpe_" + secrets.token_urlsafe(32)
    row = EmergencyAccessGrant(
        tenant_id=tenant_id, token_hash=token_hash(raw), created_by=actor,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    await session.flush()
    return row, raw


async def authenticate(
    session: AsyncSession, *, tenant_id: str, actor: str, raw: str
) -> EmergencyAccessGrant | None:
    result = await session.execute(select(EmergencyAccessGrant).where(
        EmergencyAccessGrant.tenant_id == tenant_id,
        EmergencyAccessGrant.token_hash == token_hash(raw),
        EmergencyAccessGrant.created_by == actor,
        EmergencyAccessGrant.revoked_at.is_(None),
        EmergencyAccessGrant.expires_at > datetime.now(timezone.utc),
    ))
    row = result.scalar_one_or_none()
    if row is not None:
        row.last_used_at = datetime.now(timezone.utc)
    return row


async def revoke(
    session: AsyncSession, *, tenant_id: str, actor: str, raw: str
) -> bool:
    row = await authenticate(session, tenant_id=tenant_id, actor=actor, raw=raw)
    if row is None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    return True
