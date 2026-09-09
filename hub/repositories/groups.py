from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import ClientGroup, Node

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "group"


async def list_groups(session: AsyncSession, tenant_id: str) -> list[ClientGroup]:
    rows = await session.execute(
        select(ClientGroup).where(ClientGroup.tenant_id == tenant_id).order_by(ClientGroup.name)
    )
    return list(rows.scalars())


async def get_by_slug(
    session: AsyncSession, tenant_id: str, slug: str
) -> ClientGroup | None:
    rows = await session.execute(
        select(ClientGroup).where(
            ClientGroup.tenant_id == tenant_id, ClientGroup.slug == slug
        )
    )
    return rows.scalar_one_or_none()


async def get_or_create(
    session: AsyncSession, tenant_id: str, name: str
) -> ClientGroup:
    slug = slugify(name)
    existing = await get_by_slug(session, tenant_id, slug)
    if existing:
        return existing
    group = ClientGroup(tenant_id=tenant_id, slug=slug, name=name)
    session.add(group)
    await session.flush()
    return group


async def create(
    session: AsyncSession, tenant_id: str, name: str, description: str | None = None
) -> ClientGroup:
    group = ClientGroup(
        tenant_id=tenant_id, slug=slugify(name), name=name, description=description
    )
    session.add(group)
    await session.flush()
    return group


async def delete(session: AsyncSession, group: ClientGroup) -> bool:
    """Refuse to delete a group that still owns nodes."""
    rows = await session.execute(
        select(Node.node_id).where(Node.group_id == group.id).limit(1)
    )
    if rows.first():
        return False
    await session.delete(group)
    return True
