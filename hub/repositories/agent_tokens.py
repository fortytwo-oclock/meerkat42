from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.deps import token_hash
from hub.models import AgentEnrollmentToken, ClientGroup


def generate_raw() -> str:
    return "cpa_" + secrets.token_urlsafe(32)


async def create(
    session: AsyncSession,
    *,
    tenant_id: str,
    group_id: str,
    name: str,
    created_by: str,
    expires_at: datetime | None,
) -> tuple[AgentEnrollmentToken, str]:
    raw = generate_raw()
    row = AgentEnrollmentToken(
        tenant_id=tenant_id,
        group_id=group_id,
        name=name.strip(),
        token_hash=token_hash(raw),
        created_by=created_by,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row, raw


async def authenticate(
    session: AsyncSession, raw: str
) -> tuple[AgentEnrollmentToken, ClientGroup] | None:
    now = datetime.now(timezone.utc)
    rows = await session.execute(
        select(AgentEnrollmentToken, ClientGroup)
        .join(ClientGroup, ClientGroup.id == AgentEnrollmentToken.group_id)
        .where(
            AgentEnrollmentToken.token_hash == token_hash(raw),
            AgentEnrollmentToken.revoked_at.is_(None),
            (
                AgentEnrollmentToken.expires_at.is_(None)
                | (AgentEnrollmentToken.expires_at > now)
            ),
        )
    )
    result = rows.one_or_none()
    if result is None:
        return None
    token, group = result
    token.last_used_at = now
    return token, group


async def any_active(session: AsyncSession) -> bool:
    count = await session.scalar(
        select(func.count())
        .select_from(AgentEnrollmentToken)
        .where(AgentEnrollmentToken.revoked_at.is_(None))
    )
    return bool(count)


async def list_for_tenant(
    session: AsyncSession, tenant_id: str
) -> list[tuple[AgentEnrollmentToken, str]]:
    rows = await session.execute(
        select(AgentEnrollmentToken, ClientGroup.slug)
        .join(ClientGroup, ClientGroup.id == AgentEnrollmentToken.group_id)
        .where(AgentEnrollmentToken.tenant_id == tenant_id)
        .order_by(AgentEnrollmentToken.created_at.desc())
    )
    return list(rows.tuples())


async def revoke(session: AsyncSession, tenant_id: str, token_id: str) -> bool:
    row = await session.get(AgentEnrollmentToken, token_id)
    if row is None or row.tenant_id != tenant_id or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    return True


def public(row: AgentEnrollmentToken, group_slug: str) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "group_slug": group_slug,
        "created_at": row.created_at,
        "created_by": row.created_by,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "revoked": row.revoked_at is not None,
    }
