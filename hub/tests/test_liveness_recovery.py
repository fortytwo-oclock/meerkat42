from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hub.db.base import Base
from hub.models import AlertPendingCondition, AlertRule, ClientGroup, Node, Tenant
from hub.models.alert import AlertMetric, AlertState, Comparator, Severity
from hub.models.node import NodeStatus
from hub.repositories import alerts, nodes
from hub.services import alerting


class LivenessRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(Tenant(id="tenant-a", slug="tenant-a", name="Tenant A"))
            session.add(ClientGroup(id="group-a", tenant_id="tenant-a", slug="ops", name="Ops"))
            session.add(
                Node(
                    node_id="node-a",
                    group_id="group-a",
                    hostname="node-a",
                    status=NodeStatus.OFFLINE,
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_positive_telemetry_resolves_offline_alert(self) -> None:
        async with self.sessions() as session:
            event = await alerts.open_or_update_event(
                session,
                node_id="node-a",
                type_="NODE_OFFLINE",
                subject="",
                severity=Severity.CRITICAL,
                message="offline",
                value=None,
            )
            node = await nodes.record_telemetry(session, "node-a", {"uptime_sec": 10})
            await alerting.evaluate(session, node, {"uptime_sec": 10}, [])
            await session.commit()

            self.assertEqual(event.state, AlertState.RESOLVED)
            self.assertIsNotNone(event.resolved_at)

    async def test_positive_handshake_resolves_offline_alert(self) -> None:
        async with self.sessions() as session:
            event = await alerts.open_or_update_event(
                session,
                node_id="node-a",
                type_="NODE_OFFLINE",
                subject="",
                severity=Severity.CRITICAL,
                message="offline",
                value=None,
            )
            await nodes.upsert_handshake(
                session,
                tenant_id="tenant-a",
                node_id="node-a",
                hostname="node-a",
                arch="amd64",
                client_group="default",
                agent_version="test",
                remote_ip="127.0.0.1",
            )
            await alerts.resolve_by_type(session, "node-a", "NODE_OFFLINE")
            await session.commit()

            self.assertEqual(event.state, AlertState.RESOLVED)
            self.assertIsNotNone(event.resolved_at)

    async def test_reloaded_string_rule_does_not_break_telemetry(self) -> None:
        async with self.sessions() as session:
            session.add(
                AlertRule(
                    name="CPU hot",
                    metric=AlertMetric.CPU_USAGE_PCT,
                    comparator=Comparator.GT,
                    threshold=10,
                    severity=Severity.WARNING,
                    enabled=True,
                )
            )
            await session.commit()

        async with self.sessions() as session:
            rule = (await alerts.list_rules(session, "tenant-a"))[-1]
            node = await nodes.get(session, "node-a")
            await alerting.evaluate(
                session,
                node,
                {"host_metrics": {"cpu_usage_pct": 50}},
                [rule],
            )
            await session.commit()
            active = await alerts.active_events(session, "tenant-a")
            self.assertTrue(
                any(event.type == "THRESHOLD_CPU_USAGE_PCT" for event in active)
            )

    async def test_container_update_threshold_is_a_supported_metric(self) -> None:
        self.assertEqual(
            AlertMetric("container_updates_available"),
            AlertMetric.CONTAINER_UPDATES_AVAILABLE,
        )

    async def test_duration_is_persisted_and_cleared_when_condition_resolves(self) -> None:
        async with self.sessions() as session:
            rule = AlertRule(
                name="CPU sustained",
                metric=AlertMetric.CPU_USAGE_PCT,
                comparator=Comparator.GT,
                threshold=10,
                duration_sec=60,
                severity=Severity.WARNING,
                enabled=True,
            )
            session.add(rule)
            await session.commit()
            node = await nodes.get(session, "node-a")
            await alerting.evaluate(
                session, node, {"host_metrics": {"cpu_usage_pct": 50}}, [rule]
            )
            await session.commit()
            self.assertEqual(await alerts.active_events(session, "tenant-a"), [])
            pending = (
                await session.execute(select(AlertPendingCondition))
            ).scalar_one()
            pending.first_seen_at = datetime.now(timezone.utc) - timedelta(seconds=61)
            await session.commit()

        # A new session represents a Hub restart: the timer must still be available.
        async with self.sessions() as session:
            rule = (await alerts.list_rules(session, "tenant-a"))[-1]
            node = await nodes.get(session, "node-a")
            await alerting.evaluate(
                session, node, {"host_metrics": {"cpu_usage_pct": 50}}, [rule]
            )
            await session.commit()
            active = await alerts.active_events(session, "tenant-a")
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].type, "THRESHOLD_CPU_USAGE_PCT")

            await alerting.evaluate(
                session, node, {"host_metrics": {"cpu_usage_pct": 0}}, [rule]
            )
            await session.commit()
            self.assertEqual(await alerts.active_events(session, "tenant-a"), [])
            pending = await session.execute(select(AlertPendingCondition))
            self.assertIsNone(pending.scalar_one_or_none())
