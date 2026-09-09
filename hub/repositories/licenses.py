from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import LicenseRecord


def _dt(epoch: int | None) -> datetime | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


async def get_active(session: AsyncSession, tenant_id: str) -> LicenseRecord | None:
    rows = await session.execute(
        select(LicenseRecord).where(
            LicenseRecord.tenant_id == tenant_id, LicenseRecord.is_active.is_(True)
        )
    )
    return rows.scalar_one_or_none()


async def set_active(
    session: AsyncSession,
    tenant_id: str,
    *,
    raw_token: str | None,
    claims: dict,
) -> LicenseRecord:
    """Deactivate any current license and persist the newly verified one as active."""
    await session.execute(
        update(LicenseRecord)
        .where(LicenseRecord.tenant_id == tenant_id, LicenseRecord.is_active.is_(True))
        .values(is_active=False)
    )
    record = LicenseRecord(
        tenant_id=tenant_id,
        raw_token=raw_token,
        tier=claims.get("tier", "community"),
        customer_name=claims.get("customer_name", "Community Edition User"),
        issuer=claims.get("iss"),
        subject=claims.get("sub"),
        max_nodes=int(claims.get("max_nodes", 3)),
        features=list(claims.get("features", [])),
        white_label_logo=claims.get("white_label_logo"),
        issued_at=_dt(claims.get("iat")),
        expires_at=_dt(claims.get("exp")),
        is_active=True,
    )
    session.add(record)
    await session.flush()
    return record


async def history(session: AsyncSession, tenant_id: str) -> list[LicenseRecord]:
    rows = await session.execute(
        select(LicenseRecord)
        .where(LicenseRecord.tenant_id == tenant_id)
        .order_by(LicenseRecord.applied_at.desc())
    )
    return list(rows.scalars())
