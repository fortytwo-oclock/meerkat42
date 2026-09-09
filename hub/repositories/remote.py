from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import AuditEvent, RemoteCommand


def now() -> datetime:
    return datetime.now(timezone.utc)


async def append_audit(
    session: AsyncSession, *, tenant_id: str, actor: str, action: str,
    target_type: str, target_id: str, details: dict | None = None,
) -> AuditEvent:
    row = AuditEvent(
        tenant_id=tenant_id, actor=actor, action=action,
        target_type=target_type, target_id=target_id, details=details or {},
    )
    session.add(row)
    await session.flush()
    return row


async def create(
    session: AsyncSession, *, tenant_id: str, node_id: str, action: str,
    payload: dict, requested_by: str, destructive: bool,
    approval_required: bool, idempotency_key: str | None = None,
) -> RemoteCommand:
    row = RemoteCommand(
        tenant_id=tenant_id, node_id=node_id, action=action, payload=payload,
        requested_by=requested_by, destructive=destructive,
        approval_required=approval_required, idempotency_key=idempotency_key,
        status="AWAITING_APPROVAL" if approval_required else "QUEUED",
        next_attempt_at=None if approval_required else now(),
    )
    session.add(row)
    await session.flush()
    await append_audit(
        session, tenant_id=tenant_id, actor=requested_by,
        action="command.requested", target_type="remote_command", target_id=row.id,
        details={"node_id": node_id, "command_action": action,
                 "destructive": destructive, "approval_required": approval_required},
    )
    return row


async def get_for_tenant(
    session: AsyncSession, tenant_id: str, command_id: str
) -> RemoteCommand | None:
    result = await session.execute(select(RemoteCommand).where(
        RemoteCommand.id == command_id, RemoteCommand.tenant_id == tenant_id
    ))
    return result.scalar_one_or_none()


async def get_by_idempotency(
    session: AsyncSession, tenant_id: str, key: str
) -> RemoteCommand | None:
    result = await session.execute(select(RemoteCommand).where(
        RemoteCommand.tenant_id == tenant_id, RemoteCommand.idempotency_key == key
    ))
    return result.scalar_one_or_none()


async def list_for_node(
    session: AsyncSession, tenant_id: str, node_id: str, *, limit: int = 50
) -> list[RemoteCommand]:
    result = await session.execute(select(RemoteCommand).where(
        RemoteCommand.tenant_id == tenant_id, RemoteCommand.node_id == node_id
    ).order_by(RemoteCommand.created_at.desc()).limit(limit))
    return list(result.scalars())


async def due(session: AsyncSession, *, limit: int = 50) -> list[RemoteCommand]:
    stamp = now()
    result = await session.execute(select(RemoteCommand).where(
        RemoteCommand.status == "QUEUED",
        RemoteCommand.next_attempt_at.is_not(None),
        RemoteCommand.next_attempt_at <= stamp,
    ).order_by(RemoteCommand.next_attempt_at).limit(limit))
    return list(result.scalars())


async def approve(
    session: AsyncSession, row: RemoteCommand, *, actor: str,
    require_distinct_actor: bool,
) -> None:
    if row.status != "AWAITING_APPROVAL":
        raise ValueError("command is not awaiting approval")
    if require_distinct_actor and row.requested_by == actor:
        raise PermissionError("a different owner must approve this command")
    stamp = now()
    row.approved_by, row.approved_at = actor, stamp
    row.status, row.next_attempt_at = "QUEUED", stamp
    await append_audit(
        session, tenant_id=row.tenant_id, actor=actor, action="command.approved",
        target_type="remote_command", target_id=row.id,
        details={"node_id": row.node_id, "command_action": row.action},
    )


async def cancel(session: AsyncSession, row: RemoteCommand, *, actor: str) -> None:
    if row.status not in {"QUEUED", "AWAITING_APPROVAL"}:
        raise ValueError("only queued commands can be canceled")
    row.status, row.completed_at, row.next_attempt_at = "CANCELED", now(), None
    await append_audit(
        session, tenant_id=row.tenant_id, actor=actor, action="command.canceled",
        target_type="remote_command", target_id=row.id,
        details={"node_id": row.node_id, "command_action": row.action},
    )


async def mark_dispatched(
    session: AsyncSession, row: RemoteCommand, *, timeout_seconds: int
) -> None:
    stamp = now()
    row.attempts += 1
    row.status, row.dispatched_at = "DISPATCHED", stamp
    row.timeout_at, row.next_attempt_at = stamp + timedelta(seconds=timeout_seconds), None
    await append_audit(
        session, tenant_id=row.tenant_id, actor="system",
        action="command.dispatched", target_type="remote_command", target_id=row.id,
        details={"node_id": row.node_id, "attempt": row.attempts},
    )


async def mark_unavailable(
    session: AsyncSession, row: RemoteCommand, *, max_attempts: int
) -> None:
    row.attempts += 1
    if row.attempts >= max_attempts:
        row.status, row.error, row.completed_at = "FAILED", "agent unavailable", now()
        row.next_attempt_at = None
        event = "command.failed"
    else:
        row.status, row.error = "QUEUED", "agent unavailable; retry scheduled"
        row.next_attempt_at = now() + timedelta(seconds=min(60, 2 ** row.attempts))
        event = "command.retry_scheduled"
    await append_audit(
        session, tenant_id=row.tenant_id, actor="system", action=event,
        target_type="remote_command", target_id=row.id,
        details={"node_id": row.node_id, "attempt": row.attempts},
    )


def _bounded_result(value: object, max_bytes: int) -> tuple[dict | None, str | None]:
    if value is None:
        return None, None
    encoded = json.dumps(value, separators=(",", ":"), default=str).encode()
    if len(encoded) > max_bytes:
        return None, f"agent result exceeded {max_bytes} byte limit"
    if not isinstance(value, dict):
        return {"value": value}, None
    return value, None


async def complete(
    session: AsyncSession, *, tenant_id: str, node_id: str, command_id: str,
    ok: bool, result: object, error: str | None, max_result_bytes: int,
) -> RemoteCommand | None:
    row = await get_for_tenant(session, tenant_id, command_id)
    if row is None or row.node_id != node_id or row.status != "DISPATCHED":
        return None
    bounded, size_error = _bounded_result(result, max_result_bytes)
    row.status = "SUCCEEDED" if ok and not error and not size_error else "FAILED"
    row.result = bounded
    row.error = (size_error or error or None)[:500] if (size_error or error) else None
    row.completed_at, row.timeout_at = now(), None
    await append_audit(
        session, tenant_id=tenant_id, actor=f"agent:{node_id}",
        action="command.completed" if row.status == "SUCCEEDED" else "command.failed",
        target_type="remote_command", target_id=row.id,
        details={"node_id": node_id, "command_action": row.action, "status": row.status},
    )
    return row


async def expire(session: AsyncSession) -> list[RemoteCommand]:
    stamp = now()
    result = await session.execute(select(RemoteCommand).where(
        RemoteCommand.status == "DISPATCHED", RemoteCommand.timeout_at <= stamp
    ))
    rows = list(result.scalars())
    for row in rows:
        row.status, row.error, row.completed_at = "TIMED_OUT", "agent response timed out", stamp
        row.timeout_at = None
        await append_audit(
            session, tenant_id=row.tenant_id, actor="system", action="command.timed_out",
            target_type="remote_command", target_id=row.id,
            details={"node_id": row.node_id, "command_action": row.action},
        )
    return rows


async def list_audit(
    session: AsyncSession, tenant_id: str, *, limit: int = 100
) -> list[AuditEvent]:
    result = await session.execute(select(AuditEvent).where(
        AuditEvent.tenant_id == tenant_id
    ).order_by(AuditEvent.created_at.desc()).limit(limit))
    return list(result.scalars())
