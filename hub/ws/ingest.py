"""Outbound agent WebSocket: handshake, telemetry ingestion, persistence, alerting."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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

    client_ip = websocket.client.host if websocket.client else "unknown"
    node_id = websocket.headers.get("x-node-id", "unauthenticated")

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

    active_agents[node_id] = websocket
    log.info("agent connected: %s from %s", node_id, client_ip)

    try:
        handshake = json.loads(await websocket.receive_text())
        hs_node_id = handshake.get("node_id", node_id)
        async with session_scope() as session:
            await nodes_repo.upsert_handshake(
                session,
                tenant_id=tenant_id,
                node_id=hs_node_id,
                hostname=handshake.get("hostname", "unknown"),
                client_group=handshake.get("client_group", "default"),
                agent_version=handshake.get("agent_version", "1.0.0"),
                remote_ip=client_ip,
            )
        await bus.publish({"type": "node.connected", "node_id": hs_node_id})

        while True:
            data = json.loads(await websocket.receive_text())
            data_node_id = data.get("node_id", hs_node_id)
            ts = _parse_ts(data.get("timestamp"))

            async with session_scope() as session:
                node = await nodes_repo.record_telemetry(session, data_node_id, data)
                if node is not None:
                    rules = await alerts_repo.list_rules(
                        session, tenant_id, enabled_only=True
                    )
                    await alerting.evaluate(session, node, data, rules)

            await store.write(data_node_id, ts, extract_scalars(data))
            await bus.publish(
                {
                    "type": "telemetry",
                    "node_id": data_node_id,
                    "ts": ts.isoformat(),
                    "scalars": extract_scalars(data),
                }
            )

    except WebSocketDisconnect:
        log.info("agent disconnected: %s", node_id)
    except Exception:  # pragma: no cover
        log.exception("stream error for node %s", node_id)
    finally:
        active_agents.pop(node_id, None)


def _parse_ts(raw) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
