from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from hub.config import Settings, get_settings
from hub.db.session import session_scope
from hub.repositories import remote as remote_repo
from hub.ws.bus import send_command

log = logging.getLogger("hub.remote.dispatcher")
Sender = Callable[..., Awaitable[str | None]]


async def dispatch_once(settings: Settings, sender: Sender = send_command) -> int:
    if not settings.remote_commands_enabled:
        return 0
    count = 0
    async with session_scope() as session:
        await remote_repo.expire(session)
        for row in await remote_repo.due(session):
            delivered = await sender(
                row.node_id, row.action, row.payload,
                tenant_id=row.tenant_id, command_id=row.id,
            )
            if delivered:
                await remote_repo.mark_dispatched(
                    session, row, timeout_seconds=settings.remote_command_timeout_seconds
                )
            else:
                await remote_repo.mark_unavailable(
                    session, row, max_attempts=settings.remote_command_max_attempts
                )
            count += 1
    return count


async def command_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    while not stop.is_set():
        try:
            await dispatch_once(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("remote command dispatch iteration failed")
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.remote_command_dispatch_interval_seconds
            )
        except TimeoutError:
            pass
