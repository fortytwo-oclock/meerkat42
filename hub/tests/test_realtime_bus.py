from __future__ import annotations

import unittest

from hub.ws.bus import EventBus


class FakeWebSocket:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key: str, value: str, **kwargs) -> bool:
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def publish(self, channel: str, value: str) -> int:
        self.published.append((channel, value))
        return 1

    async def eval(self, script: str, count: int, key: str, owner: str) -> int:
        if self.values.get(key) == owner:
            del self.values[key]
            return 1
        return 0


class RealtimeBusTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_event_fanout(self) -> None:
        bus = EventBus()
        queue = bus.subscribe()
        await bus.publish({"type": "telemetry", "node_id": "n-1"})
        self.assertEqual(await queue.get(), {"type": "telemetry", "node_id": "n-1"})
        bus.unsubscribe(queue)

    async def test_agent_claim_command_and_safe_release(self) -> None:
        bus = EventBus()
        first = FakeWebSocket()
        second = FakeWebSocket()
        self.assertTrue(await bus.claim_agent("n-1", first))
        self.assertFalse(await bus.claim_agent("n-1", second))
        command_id = await bus.send_command("n-1", "ping", {"value": 1})
        self.assertIsNotNone(command_id)
        self.assertEqual(first.frames[0]["command_id"], command_id)
        self.assertEqual(first.frames[0]["action"], "ping")
        await bus.release_agent("n-1", second)
        self.assertIsNotNone(await bus.send_command("n-1", "ping"))
        await bus.release_agent("n-1", first)
        self.assertIsNone(await bus.send_command("n-1", "ping"))

    async def test_invalid_lease_configuration_is_rejected(self) -> None:
        bus = EventBus()
        with self.assertRaises(ValueError):
            await bus.start("redis://unused", lease_seconds=10, refresh_seconds=10)

    async def test_command_routes_to_agent_on_another_replica(self) -> None:
        redis = FakeRedis()
        owner = EventBus()
        caller = EventBus()
        owner._redis = redis
        caller._redis = redis
        websocket = FakeWebSocket()
        self.assertTrue(await owner.claim_agent("n-remote", websocket))
        command_id = await caller.send_command("n-remote", "ping", {"seq": 1})
        self.assertIsNotNone(command_id)
        channel, raw = redis.published[-1]
        self.assertEqual(channel, owner._commands_channel(owner._instance_id))
        await owner._handle_backplane_message(channel, raw)
        self.assertEqual(websocket.frames[0]["command_id"], command_id)

    async def test_event_fans_out_without_echoing_to_origin(self) -> None:
        redis = FakeRedis()
        origin = EventBus()
        peer = EventBus()
        origin._redis = redis
        peer._redis = redis
        origin_queue = origin.subscribe()
        peer_queue = peer.subscribe()
        event = {"type": "backup.complete", "node_id": "n-1"}
        await origin.publish(event)
        self.assertEqual(await origin_queue.get(), event)
        channel, raw = redis.published[-1]
        await origin._handle_backplane_message(channel, raw)
        self.assertTrue(origin_queue.empty())
        await peer._handle_backplane_message(channel, raw)
        self.assertEqual(await peer_queue.get(), event)
