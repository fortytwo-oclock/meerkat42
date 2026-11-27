"""Background dispatcher: delivers FIRING/RESOLVED alert events to channels."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import session_scope
from hub.models import AlertEvent, ClientGroup, Node, NotificationChannel
from hub.models.alert import AlertState
from hub.notify.base import AlertPayload, ChannelError, build_sender

log = logging.getLogger("hub.notify")
_settings = get_settings()

_SEV_RANK = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}


def _rank(sev) -> int:
    return _SEV_RANK.get(getattr(sev, "value", sev), 0)


def _payload(event: AlertEvent, node: Node, group_slug: str, state: str) -> AlertPayload:
    return AlertPayload(
        event_id=event.id,
        node_id=event.node_id,
        hostname=node.hostname,
        client_group=group_slug,
        severity=getattr(event.severity, "value", event.severity),
        type=event.type,
        subject=event.subject,
        message=event.message,
        state=state,
        started_at=(event.started_at or datetime.now(timezone.utc)).isoformat(),
    )


def _channel_matches(ch: NotificationChannel, node: Node, event: AlertEvent, now: datetime) -> bool:
    if not ch.enabled:
        return False
    if ch.muted_until and ch.muted_until.replace(tzinfo=timezone.utc) > now:
        return False
    if ch.group_id and ch.group_id != node.group_id:
        return False
    if _rank(event.severity) < _rank(ch.min_severity):
        return False
    return True


async def _deliver(
    session: AsyncSession,
    channels: list[NotificationChannel],
    event: AlertEvent,
    node: Node,
    group_slug: str,
    state: str,
    now: datetime,
) -> tuple[bool, bool]:
    """Send to every matching channel. Returns (any_channel_matched, any_delivery_ok)."""
    matched = False
    any_ok = False
    for ch in channels:
        if not _channel_matches(ch, node, event, now):
            continue
        matched = True
        # Anti-spam throttle applies to FIRING only; a one-shot RESOLVED must go out.
        if (
            state != "RESOLVED"
            and ch.last_sent_at
            and (now - ch.last_sent_at.replace(tzinfo=timezone.utc))
            < timedelta(seconds=_settings.channel_min_interval_seconds)
        ):
            continue  # picked up next cycle
        try:
            cfg = dict(ch.config or {})
            if ch.kind == "push_relay" and not cfg.get("tokens"):
                from hub.models import PushDevice

                dev_rows = await session.execute(
                    select(PushDevice).where(
                        PushDevice.tenant_id == ch.tenant_id,
                        PushDevice.enabled.is_(True),
                    )
                )
                cfg["tokens"] = [
                    {"platform": d.platform, "token": d.device_token}
                    for d in dev_rows.scalars()
                ]
            sender = build_sender(ch.kind, cfg)
            await sender.send(_payload(event, node, group_slug, state))
            ch.last_sent_at = now
            ch.last_error = None
            any_ok = True
        except ChannelError as exc:
            ch.last_error = str(exc)[:500]
            log.warning("channel %s (%s) failed: %s", ch.name, ch.kind, exc)
        except Exception as exc:  # pragma: no cover
            ch.last_error = f"unexpected: {exc}"[:500]
            log.exception("channel %s crashed", ch.name)
    return matched, any_ok


async def dispatch_once() -> int:
    """One pass. Returns the number of events actioned."""
    actioned = 0
    now = datetime.now(timezone.utc)
    async with session_scope() as session:
        channels = list(
            (await session.execute(select(NotificationChannel))).scalars()
        )
        log.debug("dispatch pass: %d channel(s)", len(channels))
        if not channels:
            return 0
        by_tenant: dict[str, list[NotificationChannel]] = {}
        for ch in channels:
            by_tenant.setdefault(ch.tenant_id, []).append(ch)

        # rows: (event, node, group_slug, tenant_id)
        rows = (
            await session.execute(
                select(AlertEvent, Node, ClientGroup.slug, ClientGroup.tenant_id)
                .join(Node, AlertEvent.node_id == Node.node_id)
                .join(ClientGroup, Node.group_id == ClientGroup.id)
                .where(
                    (
                        (AlertEvent.state != AlertState.RESOLVED)
                        & AlertEvent.dispatched_at.is_(None)
                        & AlertEvent.suppressed.is_(False)
                    )
                    | (
                        (AlertEvent.state == AlertState.RESOLVED)
                        & AlertEvent.dispatched_at.is_not(None)
                        & AlertEvent.resolve_notified_at.is_(None)
                    )
                )
            )
        ).all()
        log.debug("dispatch pass: %d candidate event(s)", len(rows))

        for event, node, group_slug, tenant_id in rows:
            tenant_channels = by_tenant.get(tenant_id, [])
            if not tenant_channels:
                continue
            if event.state == AlertState.RESOLVED:
                matched, ok = await _deliver(
                    session, tenant_channels, event, node, group_slug, "RESOLVED", now
                )
                # Done once delivered, or if no channel wants it. Retry only when a
                # matching channel failed transiently.
                if ok or not matched:
                    event.resolve_notified_at = now
                    actioned += 1
            else:
                matched, ok = await _deliver(
                    session, tenant_channels, event, node, group_slug, "FIRING", now
                )
                if ok or not matched:
                    event.dispatched_at = now
                    actioned += 1
    return actioned


async def dispatch_loop(stop: asyncio.Event) -> None:
    if not _settings.dispatch_enabled:
        log.info("alert dispatch disabled")
        return
    while not stop.is_set():
        try:
            n = await dispatch_once()
            if n:
                log.info("dispatched %d alert event(s)", n)
        except Exception:  # pragma: no cover
            log.exception("dispatch pass failed")
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=_settings.dispatch_interval_seconds
            )
        except asyncio.TimeoutError:
            pass
