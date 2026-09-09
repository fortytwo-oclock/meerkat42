from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import NotificationChannel

# secret-ish config keys never echoed back to clients
_REDACT = ("password", "headers")


def redact(ch: NotificationChannel) -> dict:
    cfg = dict(ch.config or {})
    for k in _REDACT:
        if k in cfg:
            cfg[k] = "***"
    return {
        "id": ch.id,
        "name": ch.name,
        "kind": ch.kind,
        "config": cfg,
        "min_severity": getattr(ch.min_severity, "value", ch.min_severity),
        "enabled": ch.enabled,
        "muted_until": ch.muted_until,
        "group_id": ch.group_id,
        "last_error": ch.last_error,
        "last_sent_at": ch.last_sent_at,
    }


async def list_for_tenant(session: AsyncSession, tenant_id: str) -> list[NotificationChannel]:
    rows = await session.execute(
        select(NotificationChannel)
        .where(NotificationChannel.tenant_id == tenant_id)
        .order_by(NotificationChannel.created_at)
    )
    return list(rows.scalars())


async def get_for_tenant(
    session: AsyncSession, tenant_id: str, channel_id: str
) -> NotificationChannel | None:
    rows = await session.execute(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.tenant_id == tenant_id,
        )
    )
    return rows.scalar_one_or_none()


async def create(session: AsyncSession, tenant_id: str, **fields) -> NotificationChannel:
    ch = NotificationChannel(tenant_id=tenant_id, **fields)
    session.add(ch)
    await session.flush()
    return ch


def mute(ch: NotificationChannel, minutes: int) -> None:
    ch.muted_until = (
        None if minutes <= 0 else datetime.now(timezone.utc) + timedelta(minutes=minutes)
    )
