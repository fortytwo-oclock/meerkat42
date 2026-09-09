from __future__ import annotations

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hub.db.base import Base
from hub.emergency import code_at, verify_totp
from hub.models import Tenant
from hub.repositories import emergency


class TOTPTest(unittest.TestCase):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_rfc_hotp_vector_and_window(self) -> None:
        self.assertEqual(code_at(self.secret, 1), "287082")
        current = code_at(self.secret, 100)
        self.assertTrue(verify_totp(self.secret, current, timestamp=100 * 30))
        self.assertTrue(verify_totp(self.secret, current, timestamp=101 * 30))
        self.assertFalse(verify_totp(self.secret, "000000", timestamp=100 * 30))

    def test_invalid_secret_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            code_at("short", 1)


class EmergencyGrantTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(Tenant(id="tenant-a", slug="tenant-a", name="Tenant A"))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_grant_is_hashed_actor_bound_and_revocable(self) -> None:
        async with self.sessions() as session:
            row, raw = await emergency.create(
                session, tenant_id="tenant-a", actor="owner-a", ttl_seconds=300
            )
            await session.commit()
            self.assertTrue(raw.startswith("cpe_"))
            self.assertNotEqual(row.token_hash, raw)
            self.assertIsNone(await emergency.authenticate(
                session, tenant_id="tenant-a", actor="owner-b", raw=raw
            ))
            self.assertIsNotNone(await emergency.authenticate(
                session, tenant_id="tenant-a", actor="owner-a", raw=raw
            ))
            self.assertTrue(await emergency.revoke(
                session, tenant_id="tenant-a", actor="owner-a", raw=raw
            ))
            self.assertIsNone(await emergency.authenticate(
                session, tenant_id="tenant-a", actor="owner-a", raw=raw
            ))
