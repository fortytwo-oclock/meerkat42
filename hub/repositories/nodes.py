from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.models import ClientGroup, Node
from hub.models.node import NodeStatus
from hub.repositories import groups as groups_repo

_settings = get_settings()


async def get(session: AsyncSession, node_id: str) -> Node | None:
    return await session.get(Node, node_id)


async def get_for_tenant(
    session: AsyncSession, tenant_id: str, node_id: str
) -> Node | None:
    """Tenant-scoped lookup — prevents cross-tenant IDOR on node-addressed routes."""
    rows = await session.execute(
        select(Node)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(Node.node_id == node_id, ClientGroup.tenant_id == tenant_id)
    )
    return rows.scalar_one_or_none()


async def belongs_to_other_tenant(
    session: AsyncSession, tenant_id: str, node_id: str
) -> bool:
    node = await session.get(Node, node_id)
    if node is None:
        return False
    return await get_for_tenant(session, tenant_id, node_id) is None


async def list_for_tenant(
    session: AsyncSession, tenant_id: str, group_slug: str | None = None
) -> list[Node]:
    stmt = (
        select(Node)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(ClientGroup.tenant_id == tenant_id)
    )
    if group_slug:
        stmt = stmt.where(ClientGroup.slug == group_slug)
    rows = await session.execute(stmt.order_by(Node.hostname))
    return list(rows.scalars())


async def count_for_tenant(session: AsyncSession, tenant_id: str) -> int:
    rows = await session.execute(
        select(Node.node_id)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(ClientGroup.tenant_id == tenant_id)
    )
    return len(rows.all())


async def existing_ids_for_tenant(session: AsyncSession, tenant_id: str) -> set[str]:
    rows = await session.execute(
        select(Node.node_id)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(ClientGroup.tenant_id == tenant_id)
    )
    return {r[0] for r in rows.all()}


async def upsert_handshake(
    session: AsyncSession,
    tenant_id: str,
    node_id: str,
    hostname: str,
    client_group: str,
    agent_version: str,
    remote_ip: str,
) -> Node:
    group = await groups_repo.get_or_create(
        session, tenant_id, client_group or "Default"
    )
    node = await session.get(Node, node_id)
    now = datetime.now(timezone.utc)
    if node is None:
        node = Node(node_id=node_id, group_id=group.id, first_seen=now)
        session.add(node)
    elif await get_for_tenant(session, tenant_id, node_id) is None:
        raise ValueError("node_id is already registered to another tenant")
    node.hostname = hostname or node.hostname
    node.agent_version = agent_version or node.agent_version
    node.remote_ip = remote_ip
    node.status = NodeStatus.ONLINE
    node.last_seen = now
    # Only re-home the node if the agent explicitly reports a non-default group.
    if client_group and client_group.lower() != "default":
        node.group_id = group.id
    await session.flush()
    return node


async def record_telemetry(
    session: AsyncSession, node_id: str, telemetry: dict
) -> Node | None:
    node = await session.get(Node, node_id)
    if node is None:
        return None
    now = datetime.now(timezone.utc)
    node.status = NodeStatus.ONLINE
    node.last_seen = now
    node.last_telemetry = telemetry
    node.uptime_sec = int(telemetry.get("uptime_sec", node.uptime_sec) or 0)
    node.os = telemetry.get("os") or node.os
    node.kernel = telemetry.get("kernel") or node.kernel
    node.agent_version = telemetry.get("agent_version") or node.agent_version
    await session.flush()
    return node


async def mark_stale_nodes(session: AsyncSession) -> list[Node]:
    """Transition ONLINE -> STALE -> OFFLINE by last_seen age. Returns nodes that changed."""
    now = datetime.now(timezone.utc)
    stale_cut = now - timedelta(seconds=_settings.node_stale_seconds)
    offline_cut = now - timedelta(seconds=_settings.node_offline_seconds)
    changed: list[Node] = []
    rows = await session.execute(select(Node))
    for node in rows.scalars():
        if node.last_seen is None:
            continue
        last = node.last_seen
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        prev = node.status
        if last < offline_cut:
            node.status = NodeStatus.OFFLINE
        elif last < stale_cut:
            node.status = NodeStatus.STALE
        else:
            node.status = NodeStatus.ONLINE
        if node.status != prev:
            changed.append(node)
    return changed
