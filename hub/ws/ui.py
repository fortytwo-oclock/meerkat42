"""Browser-facing WebSocket: pushes live telemetry + alert events to the dashboard."""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hub.ws.bus import bus

log = logging.getLogger("hub.ui-ws")
router = APIRouter()


@router.websocket("/api/v1/ui/stream")
async def ui_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = bus.subscribe()
    try:
        await websocket.send_json({"type": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.exception("ui stream error")
    finally:
        bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close()
