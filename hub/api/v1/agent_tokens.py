from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, require_owner
from hub.repositories import agent_tokens as token_repo
from hub.repositories import groups as groups_repo

router = APIRouter()


class AgentTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    group_slug: str = Field(min_length=1, max_length=64)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


@router.get("/agent-tokens")
async def list_agent_tokens(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_owner),
) -> dict:
    rows = await token_repo.list_for_tenant(session, tenant_id)
    return {"tokens": [token_repo.public(row, slug) for row, slug in rows]}


@router.post("/agent-tokens", status_code=201)
async def create_agent_token(
    request: AgentTokenCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    group = await groups_repo.get_by_slug(session, tenant_id, request.group_slug)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=request.expires_in_days)
        if request.expires_in_days
        else None
    )
    row, raw = await token_repo.create(
        session,
        tenant_id=tenant_id,
        group_id=group.id,
        name=request.name,
        created_by=principal.label,
        expires_at=expires_at,
    )
    await session.commit()
    return {**token_repo.public(row, group.slug), "token": raw}


@router.delete("/agent-tokens/{token_id}")
async def revoke_agent_token(
    token_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_owner),
) -> dict:
    if not await token_repo.revoke(session, tenant_id, token_id):
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
    await session.commit()
    return {"revoked": token_id}
