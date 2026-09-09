from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import ApiToken
from hub.models.auth import Role

_PREFIX = "cpt_"  # control-plane token


def generate_raw() -> str:
    return _PREFIX + secrets.token_urlsafe(32)


async def list_for_tenant(session: AsyncSession, tenant_id: str) -> list[ApiToken]:
    rows = await session.execute(
        select(ApiToken)
        .where(ApiToken.tenant_id == tenant_id)
        .order_by(ApiToken.created_at.desc())
    )
    return list(rows.scalars())


async def create(
    session: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    role: Role,
    group_id: str | None,
    created_by: str,
) -> tuple[ApiToken, str]:
    from hub.deps import token_hash

    raw = generate_raw()
    tok = ApiToken(
        tenant_id=tenant_id,
        name=name,
        token_hash=token_hash(raw),
        role=role,
        group_id=group_id,
        created_by=created_by,
    )
    session.add(tok)
    await session.flush()
    return tok, raw


async def revoke(session: AsyncSession, tenant_id: str, token_id: str) -> bool:
    row = await session.execute(
        select(ApiToken).where(
            ApiToken.id == token_id, ApiToken.tenant_id == tenant_id
        )
    )
    tok = row.scalar_one_or_none()
    if tok is None or tok.revoked_at is not None:
        return False
    tok.revoked_at = datetime.now(timezone.utc)
    return True


def public(tok: ApiToken) -> dict:
    return {
        "id": tok.id,
        "name": tok.name,
        "role": getattr(tok.role, "value", tok.role),
        "group_id": tok.group_id,
        "created_at": tok.created_at,
        "created_by": tok.created_by,
        "last_used_at": tok.last_used_at,
        "revoked": tok.revoked_at is not None,
    }
