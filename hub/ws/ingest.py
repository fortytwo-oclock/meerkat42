"""Outbound agent WebSocket: handshake, telemetry ingestion, persistence, alerting."""
from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hub.config import get_settings
from hub.db.session import session_scope
from hub.repositories import alerts as alerts_repo
from hub.repositories import nodes as nodes_repo
from hub.services import alerting
from hub.timeseries.base import extract_scalars
from hub.ws.bus import active_agents, bus

log = logging.getLogger("hub.ingest")
router = APIRouter()


@router.websocket("/api/v1/agent/stream")
async def agent_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    app = websocket.app
    lm = app.state.license_manager
    store = app.state.metric_store
    tenant_id = app.state.default_tenant_id
    settings = get_settings()

    client_ip = websocket.client.host if websocket.client else "unknown"

    # 1. Authenticate the agent against the shared bootstrap token.
    presented = websocket.headers.get("x-auth-token", "")
    if settings.agent_bootstrap_token and not hmac.compare_digest(
        presented, settings.agent_bootstrap_token
    ):
        await websocket.send_json({"error": "UNAUTHORIZED", "message": "invalid agent token"})
        await websocket.close(code=4401)
        return

    # 2. Handshake first — the node identity we trust is the handshake's, and it
    #    is pinned for the rest of the connection (later frames can't reassign it).
    try:
        handshake = json.loads(await websocket.receive_text())
    except (WebSocketDisconnect, json.JSONDecodeError):
        await websocket.close(code=4400)
        return
    node_id = handshake.get("node_id") or websocket.headers.get("x-node-id")
    if not node_id:
        await websocket.send_json({"error": "BAD_HANDSHAKE", "message": "node_id required"})
        await websocket.close(code=4400)
        return

    # 3. License node-limit check against the identity we will actually persist.
    async with session_scope() as session:
        existing = await nodes_repo.existing_ids_for_tenant(session, tenant_id)
    if not lm.can_register_node(len(existing), node_id, existing):
        await websocket.send_json(
            {
                "error": "LICENSE_NODE_LIMIT_EXCEEDED",
                "message": f"Limit of {lm.max_nodes} nodes reached for tier '{lm.claims.get('tier')}'.",
            }
        )
        await websocket.close(code=4003)
        return

    if active_agents.get(node_id) is not None:
        log.warning("node %s already has a live connection; rejecting duplicate", node_id)
        await websocket.send_json({"error": "ALREADY_CONNECTED", "message": "node already streaming"})
        await websocket.close(code=4409)
        return
    active_agents[node_id] = websocket
    log.info("agent connected: %s from %s", node_id, client_ip)

    try:
        async with session_scope() as session:
            await nodes_repo.upsert_handshake(
                session,
                tenant_id=tenant_id,
                node_id=node_id,
                hostname=handshake.get("hostname", "unknown"),
                client_group=handshake.get("client_group", "default"),
                agent_version=handshake.get("agent_version", "1.0.0"),
                remote_ip=client_ip,
            )
        await bus.publish({"type": "node.connected", "node_id": node_id})

        while True:
            data = json.loads(await websocket.receive_text())
            # Ignore any node_id the frame claims — identity is pinned to the handshake.
            frame_id = data.get("node_id")
            if frame_id and frame_id != node_id:
                log.warning("node %s sent frame for %s; ignoring spoofed id", node_id, frame_id)
            ts = _parse_ts(data.get("timestamp"))

            async with session_scope() as session:
                node = await nodes_repo.record_telemetry(session, node_id, data)
                if node is not None:
                    rules = await alerts_repo.list_rules(
                        session, tenant_id, enabled_only=True
                    )
                    await alerting.evaluate(session, node, data, rules)

            await store.write(node_id, ts, extract_scalars(data))
            await bus.publish(
                {
                    "type": "telemetry",
                    "node_id": node_id,
                    "ts": ts.isoformat(),
                    "scalars": extract_scalars(data),
                }
            )

    except WebSocketDisconnect:
        log.info("agent disconnected: %s", node_id)
    except Exception:  # pragma: no cover
        log.exception("stream error for node %s", node_id)
    finally:
        if active_agents.get(node_id) is websocket:
            active_agents.pop(node_id, None)


def _parse_ts(raw) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
