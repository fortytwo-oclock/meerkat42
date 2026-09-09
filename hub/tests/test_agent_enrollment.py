from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hub.agent_auth import authenticate_agent
from hub.config import Settings
from hub.db.base import Base
from hub.models import ClientGroup, Tenant
from hub.repositories import agent_tokens


class AgentEnrollmentTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            tenant = Tenant(id="tenant-a", slug="tenant-a", name="Tenant A")
            session.add(tenant)
            await session.flush()
            group = ClientGroup(
                id="group-a", tenant_id=tenant.id, slug="servers", name="Servers"
            )
            session.add(group)
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_token_resolves_tenant_and_fixed_group(self) -> None:
        async with self.sessions() as session:
            _, raw = await agent_tokens.create(
                session,
                tenant_id="tenant-a",
                group_id="group-a",
                name="deployment token",
                created_by="owner",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            await session.commit()
        async with self.sessions() as session:
            identity = await authenticate_agent(
                session,
                presented=raw,
                default_tenant_id="default-tenant",
                settings=Settings(),
            )
            self.assertIsNotNone(identity)
            self.assertEqual(identity.tenant_id, "tenant-a")
            self.assertEqual(identity.group_slug, "servers")

    async def test_invalid_token_fails_closed_once_tokens_exist(self) -> None:
        async with self.sessions() as session:
            await agent_tokens.create(
                session,
                tenant_id="tenant-a",
                group_id="group-a",
                name="deployment token",
                created_by="owner",
                expires_at=None,
            )
            await session.commit()
        async with self.sessions() as session:
            identity = await authenticate_agent(
                session,
                presented="wrong",
                default_tenant_id="default-tenant",
                settings=Settings(),
            )
            self.assertIsNone(identity)

    async def test_legacy_bootstrap_is_default_tenant_only(self) -> None:
        async with self.sessions() as session:
            identity = await authenticate_agent(
                session,
                presented="shared-secret",
                default_tenant_id="default-tenant",
                settings=Settings(agent_bootstrap_token="shared-secret"),
            )
            self.assertEqual(identity.tenant_id, "default-tenant")
            self.assertIsNone(identity.group_slug)
