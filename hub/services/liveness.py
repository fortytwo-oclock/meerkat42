"""Background watchdogs: node liveness + time-series rollup/prune."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from hub.config import get_settings
from hub.db.session import session_scope
from hub.models.alert import AlertState, Severity
from hub.models.node import NodeStatus
from hub.repositories import alerts as alerts_repo
from hub.repositories import nodes as nodes_repo
from hub.timeseries import MetricStore

log = logging.getLogger("hub.liveness")
_settings = get_settings()


async def _liveness_tick() -> None:
    async with session_scope() as session:
        changed = await nodes_repo.mark_stale_nodes(session)
        for node in changed:
            if node.status == NodeStatus.OFFLINE:
                await alerts_repo.open_or_update_event(
                    session,
                    node_id=node.node_id,
                    type_="NODE_OFFLINE",
                    subject="",
                    severity=Severity.CRITICAL,
                    value=None,
                    message=f"Node {node.hostname} ({node.node_id}) is offline or unreachable.",
                )
            elif node.status == NodeStatus.ONLINE:
                # node came back — resolve its offline event
                await alerts_repo.resolve_by_type(
                    session, node.node_id, "NODE_OFFLINE"
                )


async def watchdog_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await _liveness_tick()
        except Exception:  # pragma: no cover
            log.exception("liveness tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_settings.watchdog_interval_seconds)
        except asyncio.TimeoutError:
            pass


async def rollup_loop(store: MetricStore, stop: asyncio.Event) -> None:
    last_prune = 0.0
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        try:
            await store.rollup(now)
            if now.timestamp() - last_prune > _settings.ts_prune_interval_seconds:
                removed = await store.prune(now)
                last_prune = now.timestamp()
                if removed:
                    log.info("pruned %d expired metric rows", removed)
        except Exception:  # pragma: no cover
            log.exception("rollup/prune tick failed")
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=_settings.ts_rollup_interval_seconds
            )
        except asyncio.TimeoutError:
            pass
