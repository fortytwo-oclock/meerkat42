from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.models import AlertEvent, AlertPendingCondition, AlertRule, ClientGroup, Node
from hub.models.alert import AlertState, Severity


async def list_rules(
    session: AsyncSession, tenant_id: str, *, enabled_only: bool = False
) -> list[AlertRule]:
    stmt = (
        select(AlertRule)
        .outerjoin(ClientGroup, AlertRule.group_id == ClientGroup.id)
        .where((AlertRule.group_id.is_(None)) | (ClientGroup.tenant_id == tenant_id))
    )
    if enabled_only:
        stmt = stmt.where(AlertRule.enabled.is_(True))
    rows = await session.execute(stmt)
    return list(rows.scalars())


async def get_rule(session: AsyncSession, rule_id: str) -> AlertRule | None:
    return await session.get(AlertRule, rule_id)


async def get_rule_for_tenant(
    session: AsyncSession, tenant_id: str, rule_id: str
) -> AlertRule | None:
    rows = await session.execute(
        select(AlertRule)
        .outerjoin(ClientGroup, AlertRule.group_id == ClientGroup.id)
        .where(
            AlertRule.id == rule_id,
            (AlertRule.group_id.is_(None)) | (ClientGroup.tenant_id == tenant_id),
        )
    )
    return rows.scalar_one_or_none()


async def get_event_for_tenant(
    session: AsyncSession, tenant_id: str, event_id: str
) -> AlertEvent | None:
    rows = await session.execute(
        select(AlertEvent)
        .join(Node, AlertEvent.node_id == Node.node_id)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(AlertEvent.id == event_id, ClientGroup.tenant_id == tenant_id)
    )
    return rows.scalar_one_or_none()


async def create_rule(session: AsyncSession, **fields) -> AlertRule:
    rule = AlertRule(**fields)
    session.add(rule)
    await session.flush()
    return rule


async def active_events(
    session: AsyncSession, tenant_id: str, *, group_slug: str | None = None
) -> list[AlertEvent]:
    stmt = (
        select(AlertEvent)
        .join(Node, AlertEvent.node_id == Node.node_id)
        .join(ClientGroup, Node.group_id == ClientGroup.id)
        .where(
            ClientGroup.tenant_id == tenant_id,
            AlertEvent.state != AlertState.RESOLVED,
        )
    )
    if group_slug:
        stmt = stmt.where(ClientGroup.slug == group_slug)
    rows = await session.execute(stmt.order_by(AlertEvent.started_at.desc()))
    return list(rows.scalars())


async def open_or_update_event(
    session: AsyncSession,
    *,
    node_id: str,
    type_: str,
    subject: str,
    severity: Severity,
    message: str,
    value: float | None,
    rule_id: str | None = None,
) -> AlertEvent:
    """Dedupe: reuse the open event for (node, type, subject), else create one."""
    rows = await session.execute(
        select(AlertEvent).where(
            AlertEvent.node_id == node_id,
            AlertEvent.type == type_,
            AlertEvent.subject == subject,
            AlertEvent.state != AlertState.RESOLVED,
        )
    )
    event = rows.scalar_one_or_none()
    if event:
        event.message = message
        event.value = value
        event.severity = severity
        return event

    # Flapping protection: if an identical alert resolved very recently, create the
    # event (so the dashboard still shows it) but mark it suppressed so the
    # dispatcher does not send another notification.
    flap_cut = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().alert_flap_window_seconds
    )
    recent_resolved = (
        await session.execute(
            select(AlertEvent)
            .where(
                AlertEvent.node_id == node_id,
                AlertEvent.type == type_,
                AlertEvent.subject == subject,
                AlertEvent.state == AlertState.RESOLVED,
                AlertEvent.resolved_at.is_not(None),
                AlertEvent.resolved_at >= flap_cut,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    event = AlertEvent(
        rule_id=rule_id,
        node_id=node_id,
        subject=subject,
        type=type_,
        severity=severity,
        message=message,
        value=value,
        state=AlertState.FIRING,
        suppressed=recent_resolved is not None,
    )
    session.add(event)
    await session.flush()
    return event


async def condition_ready(
    session: AsyncSession,
    *,
    rule: AlertRule,
    node_id: str,
    type_: str,
    subject: str,
) -> bool:
    """Return true once a persisted condition has lasted for the rule duration."""
    if rule.duration_sec <= 0:
        return True

    pending = (
        await session.execute(
            select(AlertPendingCondition).where(
                AlertPendingCondition.rule_id == rule.id,
                AlertPendingCondition.node_id == node_id,
                AlertPendingCondition.type == type_,
                AlertPendingCondition.subject == subject,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if pending is None:
        session.add(
            AlertPendingCondition(
                rule_id=rule.id,
                node_id=node_id,
                type=type_,
                subject=subject,
                first_seen_at=now,
            )
        )
        await session.flush()
        return False

    first_seen = pending.first_seen_at
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    return (now - first_seen).total_seconds() >= rule.duration_sec


async def clear_stale_conditions(
    session: AsyncSession,
    node_id: str,
    active_keys: set[tuple[str, str, str]],
) -> None:
    """Discard duration timers for conditions no longer present in telemetry."""
    rows = await session.execute(
        select(AlertPendingCondition).where(AlertPendingCondition.node_id == node_id)
    )
    for pending in rows.scalars():
        if (pending.rule_id, pending.type, pending.subject) not in active_keys:
            await session.delete(pending)


async def resolve_stale_events(
    session: AsyncSession, node_id: str, active_keys: set[tuple[str, str]]
) -> None:
    """Resolve open events for a node whose (type, subject) is no longer active."""
    rows = await session.execute(
        select(AlertEvent).where(
            AlertEvent.node_id == node_id,
            AlertEvent.state != AlertState.RESOLVED,
        )
    )
    for event in rows.scalars():
        if (event.type, event.subject) not in active_keys:
            event.state = AlertState.RESOLVED
            event.resolved_at = datetime.now(timezone.utc)


async def resolve_by_type(
    session: AsyncSession, node_id: str, type_: str, subject: str = ""
) -> None:
    rows = await session.execute(
        select(AlertEvent).where(
            AlertEvent.node_id == node_id,
            AlertEvent.type == type_,
            AlertEvent.subject == subject,
            AlertEvent.state != AlertState.RESOLVED,
        )
    )
    for event in rows.scalars():
        event.state = AlertState.RESOLVED
        event.resolved_at = datetime.now(timezone.utc)


async def acknowledge(
    session: AsyncSession, event_id: str, by: str
) -> AlertEvent | None:
    event = await session.get(AlertEvent, event_id)
    if event is None or event.state == AlertState.RESOLVED:
        return event
    event.state = AlertState.ACKNOWLEDGED
    event.acknowledged_at = datetime.now(timezone.utc)
    event.acknowledged_by = by
    return event
