"""Tiny in-process fan-out bus: telemetry ingest -> connected dashboard clients.

For a single-hub self-hosted deployment this is all that's needed. Cloud (multiple
hub replicas) would back this with Redis pub/sub — same interface.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)


bus = EventBus()

# Live agent connections, so the Hub can push HubCommand messages downstream.
active_agents: dict[str, Any] = {}
