"""Enterprise SIEM audit log export (NDJSON / JSON)."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import AuditEvent


async def fetch_audit_events(
    session: AsyncSession,
    tenant_id: str,
    *,
    since: datetime | None = None,
    limit: int = 1000,
) -> list[dict]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.created_at >= since)
    rows = await session.execute(
        stmt.order_by(AuditEvent.created_at.desc()).limit(max(1, min(limit, 10_000)))
    )
    return [
        {
            "id": e.id,
            "tenant_id": e.tenant_id,
            "actor": e.actor,
            "action": e.action,
            "target_type": e.target_type,
            "target_id": e.target_id,
            "details": e.details,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows.scalars()
    ]


def render_ndjson(events: list[dict]) -> str:
    return "\n".join(json.dumps(e, separators=(",", ":")) for e in events) + ("\n" if events else "")
