"""Browser-facing WebSocket: pushes live telemetry + alert events to the dashboard."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from hub.db.session import session_scope
from hub.deps import Principal, authenticate_presented
from hub.ws.bus import bus

log = logging.getLogger("hub.ui-ws")
router = APIRouter()


def _socket_token(websocket: WebSocket) -> str | None:
    """Read a base64url token from a WebSocket subprotocol, never the URL."""
    offered = websocket.headers.get("sec-websocket-protocol", "")
    for protocol in (part.strip() for part in offered.split(",")):
        if not protocol.startswith("token."):
            continue
        encoded = protocol[6:]
        try:
            padding = "=" * (-len(encoded) % 4)
            return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return None


def _event_visible(principal: Principal, event: dict) -> bool:
    if event.get("tenant_id") != principal.tenant_id:
        return False
    if principal.group_id is not None and event.get("group_id") != principal.group_id:
        return False
    return True


@router.websocket("/api/v1/ui/stream")
async def ui_stream(websocket: WebSocket) -> None:
    token = _socket_token(websocket)
    try:
        async with session_scope() as session:
            principal = await authenticate_presented(
                session,
                presented=token,
                default_tenant_id=websocket.app.state.default_tenant_id,
            )
    except HTTPException:
        await websocket.accept(subprotocol="meerkat42")
        await websocket.send_json({"error": "UNAUTHORIZED"})
        await websocket.close(code=4401)
        return

    await websocket.accept(subprotocol="meerkat42")
    queue = bus.subscribe()
    try:
        await websocket.send_json({"type": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                if _event_visible(principal, event):
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
