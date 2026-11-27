#!/usr/bin/env python3
"""Meerkat42 Push Notification Relay microservice (FCM / APNs / WNS).

Allows self-hosted and cloud Meerkat42 Hubs to deliver real-time alert push
notifications to official iOS, Android, macOS, and Windows Store apps without
every self-hoster managing Apple/Google/Microsoft developer signing keys.

Run:
  PUSH_RELAY_API_KEY=change-me FCM_PROJECT_ID=... FCM_ACCESS_TOKEN=... \
    uvicorn scripts.push_relay:app --host 0.0.0.0 --port 8090
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("meerkat42.push_relay")

app = FastAPI(title="Meerkat42 Push Relay", version="2.0.0")


class PushTokenTarget(BaseModel):
    platform: str
    token: str


class PushDispatchRequest(BaseModel):
    title: str
    body: str
    severity: str = "WARNING"
    state: str = "FIRING"
    node_id: str = ""
    client_group: str = ""
    event_id: str = ""
    tokens: list[PushTokenTarget] = Field(default_factory=list)


def _verify_auth(authorization: str | None) -> None:
    expected = os.getenv("PUSH_RELAY_API_KEY", "").strip()
    if not expected:
        return
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Invalid push relay API key")


async def _send_fcm(client: httpx.AsyncClient, token: str, req: PushDispatchRequest) -> bool:
    project_id = os.getenv("FCM_PROJECT_ID", "").strip()
    access_token = os.getenv("FCM_ACCESS_TOKEN", "").strip()
    if not project_id or not access_token:
        log.debug("FCM not configured; skipping android push")
        return False
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    resp = await client.post(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "message": {
                "token": token,
                "notification": {"title": req.title, "body": req.body},
                "data": {
                    "event_id": req.event_id,
                    "node_id": req.node_id,
                    "severity": req.severity,
                    "state": req.state,
                },
            }
        },
    )
    return resp.status_code < 300


async def _send_apns(client: httpx.AsyncClient, token: str, req: PushDispatchRequest) -> bool:
    bearer_jwt = os.getenv("APNS_BEARER_JWT", "").strip()
    bundle_id = os.getenv("APNS_BUNDLE_ID", "com.fortytwooclock.meerkatApp").strip()
    host = os.getenv("APNS_HOST", "https://api.push.apple.com").rstrip("/")
    if not bearer_jwt:
        log.debug("APNs not configured; skipping apple push")
        return False
    resp = await client.post(
        f"{host}/3/device/{token}",
        headers={
            "authorization": f"bearer {bearer_jwt}",
            "apns-topic": bundle_id,
            "apns-push-type": "alert",
        },
        json={
            "aps": {
                "alert": {"title": req.title, "body": req.body},
                "sound": "default",
            },
            "node_id": req.node_id,
            "event_id": req.event_id,
            "severity": req.severity,
        },
    )
    return resp.status_code < 300


async def _send_wns(client: httpx.AsyncClient, channel_uri: str, req: PushDispatchRequest) -> bool:
    from xml.sax.saxutils import escape

    wns_token = os.getenv("WNS_ACCESS_TOKEN", "").strip()
    if not wns_token or not channel_uri.startswith("https://"):
        log.debug("WNS not configured; skipping windows push")
        return False
    xml_toast = (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{escape(req.title)}</text><text>{escape(req.body)}</text>"
        "</binding></visual></toast>"
    )
    resp = await client.post(
        channel_uri,
        headers={
            "Authorization": f"Bearer {wns_token}",
            "X-WNS-Type": "wns/toast",
            "Content-Type": "text/xml",
        },
        content=xml_toast.encode("utf-8"),
    )
    return resp.status_code < 300


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "fcm_configured": bool(os.getenv("FCM_PROJECT_ID") and os.getenv("FCM_ACCESS_TOKEN")),
        "apns_configured": bool(os.getenv("APNS_BEARER_JWT")),
        "wns_configured": bool(os.getenv("WNS_ACCESS_TOKEN")),
    }


@app.post("/v1/send")
async def send_push(
    req: PushDispatchRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, int]:
    _verify_auth(authorization)
    sent = 0
    skipped = 0
    failed = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for target in req.tokens:
            plat = target.platform.strip().lower()
            try:
                ok = False
                if plat == "android":
                    ok = await _send_fcm(client, target.token, req)
                elif plat in {"ios", "macos"}:
                    ok = await _send_apns(client, target.token, req)
                elif plat == "windows":
                    ok = await _send_wns(client, target.token, req)
                else:
                    skipped += 1
                    continue
                if ok:
                    sent += 1
                else:
                    skipped += 1
            except httpx.HTTPError:
                failed += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}
