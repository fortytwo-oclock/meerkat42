"""Realtime event fan-out and reverse-tunnel command routing.

Without REDIS_URL this remains a zero-dependency in-process bus. Cloud replicas
share events, commands, and expiring agent ownership leases through Redis.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

log = logging.getLogger("hub.ws.bus")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._agents: dict[str, Any] = {}
        self._redis: Any = None
        self._pubsub: Any = None
        self._tasks: list[asyncio.Task] = []
        self._instance_id = uuid.uuid4().hex
        self._prefix = "control-plane"
        self._lease_seconds = 45
        self._refresh_seconds = 15

    async def start(
        self,
        redis_url: str,
        *,
        prefix: str = "control-plane",
        lease_seconds: int = 45,
        refresh_seconds: int = 15,
    ) -> None:
        if not redis_url:
            return
        if refresh_seconds <= 0 or lease_seconds <= refresh_seconds:
            raise ValueError("agent lease must be longer than its positive refresh interval")
        from redis.asyncio import from_url

        self._prefix = prefix.strip(":") or "control-plane"
        self._lease_seconds = lease_seconds
        self._refresh_seconds = refresh_seconds
        self._redis = from_url(redis_url, decode_responses=True)
        await self._redis.ping()
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(
            self._events_channel, self._commands_channel(self._instance_id)
        )
        self._tasks = [
            asyncio.create_task(self._listen(), name="redis-realtime-listener"),
            asyncio.create_task(self._refresh_leases(), name="redis-agent-leases"),
        ]
        log.info("Redis realtime backplane ready (instance=%s)", self._instance_id)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for node_id, websocket in list(self._agents.items()):
            await self.release_agent(node_id, websocket)
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    @property
    def _events_channel(self) -> str:
        return f"{self._prefix}:events"

    def _commands_channel(self, instance_id: str) -> str:
        return f"{self._prefix}:commands:{instance_id}"

    def _agent_key(self, node_id: str) -> str:
        return f"{self._prefix}:agent:{node_id}"

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish_local(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def publish(self, event: dict[str, Any]) -> None:
        self._publish_local(event)
        if self._redis is not None:
            envelope = {"origin": self._instance_id, "event": event}
            try:
                await self._redis.publish(self._events_channel, json.dumps(envelope))
            except Exception:
                # Telemetry persistence and local dashboards remain useful during
                # a transient backplane outage.
                log.warning("could not publish realtime event to Redis", exc_info=True)

    async def claim_agent(self, node_id: str, websocket: Any) -> bool:
        if node_id in self._agents:
            return False
        if self._redis is not None:
            claimed = await self._redis.set(
                self._agent_key(node_id),
                self._instance_id,
                ex=self._lease_seconds,
                nx=True,
            )
            if not claimed:
                return False
        self._agents[node_id] = websocket
        return True

    async def release_agent(self, node_id: str, websocket: Any) -> None:
        if self._agents.get(node_id) is not websocket:
            return
        self._agents.pop(node_id, None)
        if self._redis is not None:
            try:
                # Delete only our lease; a reconnect may already belong elsewhere.
                await self._redis.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    self._agent_key(node_id),
                    self._instance_id,
                )
            except Exception:
                log.warning("could not release Redis agent lease", exc_info=True)

    async def send_command(
        self, node_id: str, action: str, payload: dict | None = None
    ) -> str | None:
        command_id = uuid.uuid4().hex
        frame = {
            "type": "command",
            "command_id": command_id,
            "action": action,
            "payload": payload or {},
        }
        websocket = self._agents.get(node_id)
        if websocket is not None:
            await websocket.send_json(frame)
            return command_id
        if self._redis is None:
            return None
        try:
            instance_id = await self._redis.get(self._agent_key(node_id))
            if not instance_id:
                return None
            delivered = await self._redis.publish(
                self._commands_channel(instance_id),
                json.dumps({"node_id": node_id, "frame": frame}),
            )
            return command_id if delivered else None
        except Exception:
            log.warning("could not route agent command through Redis", exc_info=True)
            return None

    async def _listen(self) -> None:
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await self._handle_backplane_message(
                        message["channel"], message["data"]
                    )
                except Exception:
                    log.exception("invalid or undeliverable realtime backplane message")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Redis realtime listener stopped unexpectedly")

    async def _handle_backplane_message(self, channel: str, raw: str) -> None:
        data = json.loads(raw)
        if channel == self._events_channel:
            if data.get("origin") != self._instance_id:
                self._publish_local(data["event"])
            return
        websocket = self._agents.get(data["node_id"])
        if websocket is not None:
            await websocket.send_json(data["frame"])

    async def _refresh_leases(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_seconds)
            try:
                for node_id in list(self._agents):
                    key = self._agent_key(node_id)
                    owner = await self._redis.get(key)
                    if owner == self._instance_id:
                        await self._redis.expire(key, self._lease_seconds)
                    else:
                        log.warning("lost distributed lease for agent %s", node_id)
                        websocket = self._agents.pop(node_id, None)
                        if websocket is not None:
                            with contextlib.suppress(Exception):
                                await websocket.close(code=1012)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("could not refresh Redis agent leases", exc_info=True)


bus = EventBus()

# Compatibility alias for diagnostics and existing integrations.
active_agents = bus._agents


async def send_command(
    node_id: str, action: str, payload: dict | None = None
) -> str | None:
    return await bus.send_command(node_id, action, payload)
