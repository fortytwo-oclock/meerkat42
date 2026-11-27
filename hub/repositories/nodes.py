from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.models import ClientGroup, Node
from hub.models.node import DeviceClass, NodeStatus, VALID_DEVICE_CLASSES, VALID_PLATFORMS
from hub.repositories import groups as groups_repo

_settings = get_settings()


def _normalize_device_class(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    val = raw.strip().lower()
    return val if val in VALID_DEVICE_CLASSES else None


def _normalize_platform(raw_platform: object, raw_os: object = None) -> str | None:
    for candidate in (raw_platform, raw_os):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        low = candidate.strip().lower()
        if low in VALID_PLATFORMS:
            return low
        if "darwin" in low or "mac" in low:
            return "macos"
        if "win" in low:
            return "windows"
        if "android" in low:
            return "android"
        if low.startswith("ios") or "iphone" in low or "ipad" in low:
            return "ios"
        if "linux" in low or "ubuntu" in low or "debian" in low or "alpine" in low:
            return "linux"
    return None


def _sanitize_location(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    lat = raw.get("lat")
    lon = raw.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0):
        return None
    clean: dict[str, object] = {
        "lat": float(lat),
        "lon": float(lon),
        "updated_at": str(raw.get("updated_at") or datetime.now(timezone.utc).isoformat()),
    }
    for num_key in ("accuracy_m", "altitude_m", "speed_mps"):
        if isinstance(raw.get(num_key), (int, float)):
            clean[num_key] = float(raw[num_key])
    for str_key in ("zone", "source"):
        if isinstance(raw.get(str_key), str) and raw[str_key].strip():
            clean[str_key] = raw[str_key].strip()[:128]
    return clean


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
    session: AsyncSession,
    tenant_id: str,
    group_slug: str | None = None,
    device_class: str | None = None,
    platform: str | None = None,
) -> list[Node]:
    stmt = (
        select(Node)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(ClientGroup.tenant_id == tenant_id)
    )
    if group_slug:
        stmt = stmt.where(ClientGroup.slug == group_slug)
    if device_class:
        stmt = stmt.where(Node.device_class == device_class.strip().lower())
    if platform:
        stmt = stmt.where(Node.platform == platform.strip().lower())
    rows = await session.execute(stmt.order_by(Node.hostname))
    return list(rows.scalars())


async def count_for_tenant(session: AsyncSession, tenant_id: str) -> int:
    rows = await session.execute(
        select(Node.node_id)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(ClientGroup.tenant_id == tenant_id)
    )
    return len(rows.all())


async def delete(session: AsyncSession, node: Node) -> None:
    """Delete a retired node and its database-backed history via FK cascades."""
    await session.delete(node)
    await session.flush()


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
    arch: str,
    client_group: str,
    agent_version: str,
    remote_ip: str,
    device_class: str | None = None,
    platform: str | None = None,
    hardware_info: dict | None = None,
) -> Node:
    group = await groups_repo.get_or_create(
        session, tenant_id, client_group or "Default"
    )
    node = await session.get(Node, node_id)
    now = datetime.now(timezone.utc)
    if node is None:
        node = Node(
            node_id=node_id,
            group_id=group.id,
            first_seen=now,
            device_class=_normalize_device_class(device_class) or DeviceClass.SERVER.value,
        )
        session.add(node)
    elif await get_for_tenant(session, tenant_id, node_id) is None:
        raise ValueError("node_id is already registered to another tenant")
    node.hostname = hostname or node.hostname
    node.arch = arch or node.arch
    if norm_dc := _normalize_device_class(device_class):
        node.device_class = norm_dc
    if norm_plat := _normalize_platform(platform):
        node.platform = norm_plat
    if isinstance(hardware_info, dict) and hardware_info:
        node.hardware_info = hardware_info
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
    node.arch = telemetry.get("arch") or node.arch
    node.kernel = telemetry.get("kernel") or node.kernel
    node.agent_version = telemetry.get("agent_version") or node.agent_version
    if norm_dc := _normalize_device_class(telemetry.get("device_class")):
        node.device_class = norm_dc
    if norm_plat := _normalize_platform(telemetry.get("platform"), telemetry.get("os")):
        node.platform = norm_plat
    if isinstance(telemetry.get("hardware"), dict) and telemetry["hardware"]:
        node.hardware_info = telemetry["hardware"]
    if loc := _sanitize_location(telemetry.get("location")):
        node.last_location = loc
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
