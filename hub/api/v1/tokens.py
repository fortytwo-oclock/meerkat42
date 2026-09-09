from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, require_owner
from hub.models.auth import Role
from hub.repositories import groups as groups_repo
from hub.repositories import tokens as tokens_repo

router = APIRouter()


class TokenCreate(BaseModel):
    name: str
    role: str = "viewer"  # viewer | admin | owner
    group_slug: str | None = None  # viewer scoping


@router.get("/tokens")
async def list_tokens(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_owner),
) -> dict:
    rows = await tokens_repo.list_for_tenant(session, tenant_id)
    return {"tokens": [tokens_repo.public(t) for t in rows]}


@router.post("/tokens", status_code=201)
async def create_token(
    req: TokenCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    try:
        role = Role(req.role)
    except ValueError:
        raise HTTPException(status_code=422, detail="role must be viewer|admin|owner")

    group_id = None
    if req.group_slug:
        if role != Role.VIEWER:
            raise HTTPException(status_code=422, detail="only viewer tokens can be group-scoped")
        group = await groups_repo.get_by_slug(session, tenant_id, req.group_slug)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        group_id = group.id

    tok, raw = await tokens_repo.create(
        session,
        tenant_id=tenant_id,
        name=req.name,
        role=role,
        group_id=group_id,
        created_by=principal.label,
    )
    await session.commit()
    # raw token returned exactly once
    return {**tokens_repo.public(tok), "token": raw}


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_owner),
) -> dict:
    if not await tokens_repo.revoke(session, tenant_id, token_id):
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
    await session.commit()
    return {"revoked": token_id}
