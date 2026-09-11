from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hub.api.v1.nodes import delete_node
from hub.db.base import Base
from hub.deps import Principal
from hub.models import AuditEvent, ClientGroup, ConfigBackup, MetricPoint, Node, Tenant
from hub.models.auth import Role
from hub.models.node import NodeStatus


class NodeDeletionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(Tenant(id="tenant-a", slug="tenant-a", name="Tenant A"))
            session.add(
                ClientGroup(id="group-a", tenant_id="tenant-a", slug="ops", name="Ops")
            )
            session.add_all(
                [
                    Node(
                        node_id="offline-node",
                        group_id="group-a",
                        hostname="retired-host",
                        status=NodeStatus.OFFLINE,
                    ),
                    Node(
                        node_id="online-node",
                        group_id="group-a",
                        hostname="active-host",
                        status=NodeStatus.ONLINE,
                    ),
                ]
            )
            await session.commit()
            session.add(
                MetricPoint(
                    node_id="offline-node",
                    metric="cpu_usage_pct",
                    ts=datetime.now(timezone.utc),
                    value=12.5,
                )
            )
            session.add(
                ConfigBackup(
                    tenant_id="tenant-a",
                    node_id="offline-node",
                    command_id="backup-command",
                    requested_by="test-admin",
                )
            )
            await session.commit()
        self.principal = Principal(Role.ADMIN, "tenant-a", None, "test-admin")

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_offline_node_can_be_forgotten_and_is_audited(self) -> None:
        async with self.sessions() as session:
            result = await delete_node(
                "offline-node",
                session=session,
                tenant_id="tenant-a",
                principal=self.principal,
            )
            self.assertEqual(result, {"deleted": "offline-node"})
            self.assertIsNone(await session.get(Node, "offline-node"))
            audit = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.action == "node.deleted")
                )
            ).scalar_one()
            self.assertEqual(audit.actor, "test-admin")
            self.assertEqual(audit.target_id, "offline-node")
            self.assertEqual(audit.details["hostname"], "retired-host")
            metric_count = (
                await session.execute(select(func.count()).select_from(MetricPoint))
            ).scalar_one()
            backup_count = (
                await session.execute(select(func.count()).select_from(ConfigBackup))
            ).scalar_one()
            self.assertEqual(metric_count, 0)
            self.assertEqual(backup_count, 0)

    async def test_online_node_is_rejected(self) -> None:
        async with self.sessions() as session:
            with self.assertRaises(HTTPException) as raised:
                await delete_node(
                    "online-node",
                    session=session,
                    tenant_id="tenant-a",
                    principal=self.principal,
                )
            self.assertEqual(raised.exception.status_code, 409)
            count = (
                await session.execute(
                    select(func.count()).select_from(Node).where(Node.node_id == "online-node")
                )
            ).scalar_one()
            self.assertEqual(count, 1)

    async def test_cross_tenant_node_is_not_found(self) -> None:
        async with self.sessions() as session:
            with self.assertRaises(HTTPException) as raised:
                await delete_node(
                    "offline-node",
                    session=session,
                    tenant_id="tenant-b",
                    principal=Principal(Role.ADMIN, "tenant-b", None, "other-admin"),
                )
            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
