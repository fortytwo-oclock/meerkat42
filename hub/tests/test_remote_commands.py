from __future__ import annotations

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hub.db.base import Base
from hub.models import ClientGroup, Node, Tenant
from hub.remote.policy import CommandPolicyError, validate_command
from hub.remote.dispatcher import dispatch_once
from hub.repositories import remote
from hub.config import Settings


class RemoteCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(Tenant(id="tenant-a", slug="tenant-a", name="Tenant A"))
            session.add(ClientGroup(id="group-a", tenant_id="tenant-a", slug="ops", name="Ops"))
            session.add(Node(node_id="node-a", group_id="group-a", hostname="node-a"))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_dispatch_and_bounded_completion_are_audited(self) -> None:
        async with self.sessions() as session:
            row = await remote.create(
                session, tenant_id="tenant-a", node_id="node-a", action="ping",
                payload={}, requested_by="admin", destructive=False,
                approval_required=False, idempotency_key="once",
            )
            await remote.mark_dispatched(session, row, timeout_seconds=60)
            completed = await remote.complete(
                session, tenant_id="tenant-a", node_id="node-a", command_id=row.id,
                ok=True, result={"pong": True}, error=None, max_result_bytes=1024,
            )
            await session.commit()
            self.assertEqual(completed.status, "SUCCEEDED")
            self.assertEqual(completed.result, {"pong": True})
            self.assertEqual(completed.attempts, 1)
            self.assertEqual(len(await remote.list_audit(session, "tenant-a")), 3)

    async def test_cross_node_result_and_duplicate_result_are_ignored(self) -> None:
        async with self.sessions() as session:
            row = await remote.create(
                session, tenant_id="tenant-a", node_id="node-a", action="ping",
                payload={}, requested_by="admin", destructive=False,
                approval_required=False,
            )
            await remote.mark_dispatched(session, row, timeout_seconds=60)
            wrong = await remote.complete(
                session, tenant_id="tenant-a", node_id="other", command_id=row.id,
                ok=True, result={}, error=None, max_result_bytes=1024,
            )
            self.assertIsNone(wrong)
            first = await remote.complete(
                session, tenant_id="tenant-a", node_id="node-a", command_id=row.id,
                ok=True, result={}, error=None, max_result_bytes=1024,
            )
            duplicate = await remote.complete(
                session, tenant_id="tenant-a", node_id="node-a", command_id=row.id,
                ok=False, result={}, error="late", max_result_bytes=1024,
            )
            self.assertEqual(first.status, "SUCCEEDED")
            self.assertIsNone(duplicate)

    async def test_two_person_approval_requires_distinct_actor(self) -> None:
        async with self.sessions() as session:
            row = await remote.create(
                session, tenant_id="tenant-a", node_id="node-a", action="future-danger",
                payload={}, requested_by="owner-a", destructive=True,
                approval_required=True,
            )
            with self.assertRaises(PermissionError):
                await remote.approve(session, row, actor="owner-a", require_distinct_actor=True)
            await remote.approve(session, row, actor="owner-b", require_distinct_actor=True)
            self.assertEqual(row.status, "QUEUED")
            self.assertEqual(row.approved_by, "owner-b")

    async def test_oversized_result_fails_closed(self) -> None:
        async with self.sessions() as session:
            row = await remote.create(
                session, tenant_id="tenant-a", node_id="node-a", action="ping",
                payload={}, requested_by="admin", destructive=False,
                approval_required=False,
            )
            await remote.mark_dispatched(session, row, timeout_seconds=60)
            await remote.complete(
                session, tenant_id="tenant-a", node_id="node-a", command_id=row.id,
                ok=True, result={"data": "x" * 100}, error=None, max_result_bytes=10,
            )
            self.assertEqual(row.status, "FAILED")
            self.assertIsNone(row.result)
            self.assertIn("exceeded", row.error)

    async def test_dispatch_commits_before_fast_agent_result(self) -> None:
        async with self.sessions() as session:
            row = await remote.create(
                session, tenant_id="tenant-a", node_id="node-a", action="ping",
                payload={}, requested_by="admin", destructive=False,
                approval_required=False,
            )
            command_id = row.id
            await session.commit()

        async def immediate_agent_result(node_id, action, payload, **kwargs):
            async with self.sessions() as agent_session:
                completed = await remote.complete(
                    agent_session,
                    tenant_id=kwargs["tenant_id"],
                    node_id=node_id,
                    command_id=kwargs["command_id"],
                    ok=True,
                    result={"pong": "ok"},
                    error=None,
                    max_result_bytes=1024,
                )
                self.assertIsNotNone(completed)
                await agent_session.commit()
            return kwargs["command_id"]

        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            remote_commands_enabled=True,
        )
        # dispatch_once uses the application session factory, so temporarily
        # point it at this test's isolated database.
        from hub.remote import dispatcher
        original_scope = dispatcher.session_scope

        async def test_scope():
            async with self.sessions() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        from contextlib import asynccontextmanager
        dispatcher.session_scope = asynccontextmanager(test_scope)
        try:
            self.assertEqual(
                await dispatch_once(settings, sender=immediate_agent_result), 1
            )
        finally:
            dispatcher.session_scope = original_scope

        async with self.sessions() as session:
            completed = await remote.get_for_tenant(session, "tenant-a", command_id)
            self.assertEqual(completed.status, "SUCCEEDED")
            self.assertEqual(completed.result, {"pong": "ok"})


class RemotePolicyTest(unittest.TestCase):
    def test_only_allowlisted_schema_is_accepted(self) -> None:
        policy, payload = validate_command("ping", {})
        self.assertFalse(policy.destructive)
        self.assertEqual(payload, {})
        with self.assertRaises(CommandPolicyError):
            validate_command("shell", {"command": "whoami"})
        with self.assertRaises(CommandPolicyError):
            validate_command("ping", {"surprise": True})

    def test_proxmox_actions_have_strict_schema_and_risk(self) -> None:
        payload = {"node": "pve-a", "guest_type": "lxc", "vmid": 101}
        start, normalized = validate_command("proxmox_start", payload)
        reboot, _ = validate_command("proxmox_reboot", payload)
        self.assertFalse(start.destructive)
        self.assertTrue(reboot.destructive)
        self.assertEqual(normalized, payload)
        with self.assertRaises(CommandPolicyError):
            validate_command("proxmox_shutdown", {**payload, "force": True})

    def test_docker_pull_is_tag_only_and_long_running(self) -> None:
        policy, payload = validate_command(
            "docker_pull_image", {"image": "registry.example/app:v2"}
        )
        self.assertFalse(policy.destructive)
        self.assertEqual(policy.timeout_seconds, 900)
        self.assertEqual(payload["image"], "registry.example/app:v2")
        with self.assertRaises(CommandPolicyError):
            validate_command("docker_pull_image", {"image": "app@sha256:abc"})

    def test_diagnostics_are_fixed_and_emergency_only(self) -> None:
        policy, payload = validate_command(
            "diagnostic_run", {"diagnostic": "failed_services"}
        )
        self.assertTrue(policy.emergency)
        self.assertEqual(payload, {"diagnostic": "failed_services"})
        with self.assertRaises(CommandPolicyError):
            validate_command("diagnostic_run", {"diagnostic": "shell"})
