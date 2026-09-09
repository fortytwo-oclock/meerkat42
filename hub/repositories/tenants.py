from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.models import ClientGroup, Tenant

_settings = get_settings()


async def get_default_tenant(session: AsyncSession) -> Tenant:
    row = await session.execute(
        select(Tenant).where(Tenant.slug == _settings.default_tenant_slug)
    )
    return row.scalar_one()


async def get_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
    row = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return row.scalar_one_or_none()


async def get_or_create_named(
    session: AsyncSession, slug: str, name: str
) -> tuple[Tenant, ClientGroup]:
    """Create a billing-provisioned tenant and its default group if needed."""
    tenant = await get_by_slug(session, slug)
    if tenant is None:
        tenant = Tenant(slug=slug, name=name)
        session.add(tenant)
        await session.flush()
    row = await session.execute(
        select(ClientGroup).where(
            ClientGroup.tenant_id == tenant.id,
            ClientGroup.slug == _settings.default_group_slug,
        )
    )
    group = row.scalar_one_or_none()
    if group is None:
        group = ClientGroup(
            tenant_id=tenant.id,
            slug=_settings.default_group_slug,
            name="Default",
            description="Ungrouped nodes",
        )
        session.add(group)
        await session.flush()
    return tenant, group


async def ensure_bootstrap(session: AsyncSession) -> tuple[Tenant, ClientGroup]:
    """Create the default tenant + default client group if missing (self-hosted first run)."""
    row = await session.execute(
        select(Tenant).where(Tenant.slug == _settings.default_tenant_slug)
    )
    tenant = row.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(slug=_settings.default_tenant_slug, name="Default")
        session.add(tenant)
        await session.flush()

    row = await session.execute(
        select(ClientGroup).where(
            ClientGroup.tenant_id == tenant.id,
            ClientGroup.slug == _settings.default_group_slug,
        )
    )
    group = row.scalar_one_or_none()
    if group is None:
        group = ClientGroup(
            tenant_id=tenant.id,
            slug=_settings.default_group_slug,
            name="Default",
            description="Ungrouped nodes",
        )
        session.add(group)
        await session.flush()

    return tenant, group
