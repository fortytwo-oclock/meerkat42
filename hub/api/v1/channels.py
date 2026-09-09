from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import get_tenant_id, require_admin
from hub.models.alert import Severity
from hub.notify.base import AlertPayload, ChannelError, build_sender
from hub.notify.urlguard import validate_webhook_url
from hub.repositories import channels as ch_repo
from hub.repositories import groups as groups_repo
from hub.schemas import ChannelCreate, ChannelUpdate, MuteRequest

log = logging.getLogger("hub.channels")
router = APIRouter()

_KINDS = {"discord", "slack", "smtp", "webhook"}
_URL_KINDS = {"discord", "slack", "webhook"}


async def _check_url(kind: str, config: dict) -> None:
    if kind in _URL_KINDS:
        try:
            await asyncio.to_thread(validate_webhook_url, config.get("url", ""), kind)
        except ChannelError as exc:
            raise HTTPException(status_code=422, detail=str(exc))


@router.get("/channels")
async def list_channels(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    rows = await ch_repo.list_for_tenant(session, tenant_id)
    return {"channels": [ch_repo.redact(c) for c in rows]}


@router.post("/channels", status_code=201)
async def create_channel(
    req: ChannelCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    if req.kind not in _KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(_KINDS)}")
    try:
        Severity(req.min_severity)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid min_severity")
    await _check_url(req.kind, req.config)

    group_id = None
    if req.group_slug:
        group = await groups_repo.get_by_slug(session, tenant_id, req.group_slug)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        group_id = group.id

    ch = await ch_repo.create(
        session,
        tenant_id,
        name=req.name,
        kind=req.kind,
        config=req.config,
        min_severity=Severity(req.min_severity),
        enabled=req.enabled,
        group_id=group_id,
    )
    await session.commit()
    return ch_repo.redact(ch)


@router.patch("/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    req: ChannelUpdate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    ch = await ch_repo.get_for_tenant(session, tenant_id, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    if req.name is not None:
        ch.name = req.name
    if req.config is not None:
        await _check_url(ch.kind, req.config)
        ch.config = req.config
    if req.enabled is not None:
        ch.enabled = req.enabled
    if req.min_severity is not None:
        try:
            ch.min_severity = Severity(req.min_severity)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid min_severity")
    await session.commit()
    return ch_repo.redact(ch)


@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    ch = await ch_repo.get_for_tenant(session, tenant_id, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    await session.delete(ch)
    await session.commit()
    return {"deleted": channel_id}


@router.post("/channels/{channel_id}/mute")
async def mute_channel(
    channel_id: str,
    req: MuteRequest,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    ch = await ch_repo.get_for_tenant(session, tenant_id, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch_repo.mute(ch, req.minutes)
    await session.commit()
    return {"id": ch.id, "muted_until": ch.muted_until}


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    ch = await ch_repo.get_for_tenant(session, tenant_id, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    payload = AlertPayload(
        event_id="test",
        node_id="test-node",
        hostname="test-node",
        client_group="test",
        severity="INFO",
        type="TEST_NOTIFICATION",
        subject="",
        message="This is a test notification from the Control Plane Hub.",
        state="FIRING",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        await build_sender(ch.kind, ch.config or {}).send(payload)
    except ChannelError as exc:
        ch.last_error = str(exc)[:500]
        await session.commit()
        log.warning("test delivery failed for channel %s: %s", ch.id, exc)
        raise HTTPException(status_code=502, detail="delivery failed")
    ch.last_error = None
    ch.last_sent_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}
