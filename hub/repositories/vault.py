from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import ConfigBackup


async def create_pending(
    session: AsyncSession,
    *,
    tenant_id: str,
    node_id: str,
    command_id: str,
    requested_by: str,
) -> ConfigBackup:
    backup = ConfigBackup(
        tenant_id=tenant_id,
        node_id=node_id,
        command_id=command_id,
        requested_by=requested_by,
        status="PENDING",
    )
    session.add(backup)
    await session.flush()
    return backup


async def get_by_command(
    session: AsyncSession, command_id: str
) -> ConfigBackup | None:
    row = await session.execute(
        select(ConfigBackup).where(ConfigBackup.command_id == command_id)
    )
    return row.scalar_one_or_none()


async def get_for_tenant(
    session: AsyncSession, tenant_id: str, backup_id: str
) -> ConfigBackup | None:
    row = await session.execute(
        select(ConfigBackup).where(
            ConfigBackup.id == backup_id, ConfigBackup.tenant_id == tenant_id
        )
    )
    return row.scalar_one_or_none()


async def list_for_node(
    session: AsyncSession, tenant_id: str, node_id: str
) -> list[ConfigBackup]:
    rows = await session.execute(
        select(ConfigBackup)
        .where(
            ConfigBackup.tenant_id == tenant_id, ConfigBackup.node_id == node_id
        )
        .order_by(ConfigBackup.requested_at.desc())
    )
    return list(rows.scalars())


async def complete(
    session: AsyncSession,
    command_id: str,
    *,
    files: list[dict],
    key_fingerprint: str | None,
    error: str | None,
) -> ConfigBackup | None:
    backup = await get_by_command(session, command_id)
    if backup is None:
        return None
    now = datetime.now(timezone.utc)
    backup.completed_at = now
    if error:
        backup.status = "ERROR"
        backup.error = error[:500]
        return backup
    backup.status = "COMPLETE"
    backup.files = files
    backup.file_count = len(files)
    backup.total_bytes = sum(int(f.get("size", 0)) for f in files)
    backup.key_fingerprint = key_fingerprint
    return backup
