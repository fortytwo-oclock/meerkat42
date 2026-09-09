from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, require_admin, require_owner
from hub.repositories import nodes as nodes_repo
from hub.repositories import remote as remote_repo
from hub.remote.policy import CommandPolicyError, validate_command

router = APIRouter()


class CommandCreate(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


def public(row) -> dict:
    return {
        "id": row.id, "node_id": row.node_id, "action": row.action,
        "payload": row.payload, "status": row.status,
        "requested_by": row.requested_by, "destructive": row.destructive,
        "approval_required": row.approval_required,
        "approved_by": row.approved_by, "approved_at": row.approved_at,
        "attempts": row.attempts, "dispatched_at": row.dispatched_at,
        "completed_at": row.completed_at, "result": row.result,
        "error": row.error, "created_at": row.created_at,
    }


@router.post("/nodes/{node_id}/commands", status_code=status.HTTP_202_ACCEPTED)
async def create_command(
    node_id: str, request: CommandCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin),
) -> dict:
    settings = get_settings()
    if not settings.remote_commands_enabled:
        raise HTTPException(status_code=503, detail="remote commands are disabled")
    if await nodes_repo.get_for_tenant(session, tenant_id, node_id) is None:
        raise HTTPException(status_code=404, detail="Node not found")
    if idempotency_key:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 255:
            raise HTTPException(status_code=400, detail="invalid Idempotency-Key")
        existing = await remote_repo.get_by_idempotency(session, tenant_id, idempotency_key)
        if existing:
            if existing.node_id != node_id or existing.action != request.action or existing.payload != request.payload:
                raise HTTPException(status_code=409, detail="Idempotency-Key was used for a different command")
            return public(existing)
    try:
        policy, normalized_payload = validate_command(request.action, request.payload)
    except CommandPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    approval_required = policy.destructive and settings.remote_command_two_person_approval
    row = await remote_repo.create(
        session, tenant_id=tenant_id, node_id=node_id, action=request.action,
        payload=normalized_payload, requested_by=principal.label,
        destructive=policy.destructive, approval_required=approval_required,
        idempotency_key=idempotency_key,
    )
    await session.commit()
    return public(row)


@router.get("/nodes/{node_id}/commands")
async def list_commands(
    node_id: str, limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
) -> dict:
    if await nodes_repo.get_for_tenant(session, tenant_id, node_id) is None:
        raise HTTPException(status_code=404, detail="Node not found")
    rows = await remote_repo.list_for_node(session, tenant_id, node_id, limit=limit)
    return {"commands": [public(row) for row in rows]}


@router.get("/commands/{command_id}")
async def get_command(
    command_id: str, session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id), _: Principal = Depends(require_admin),
) -> dict:
    row = await remote_repo.get_for_tenant(session, tenant_id, command_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Command not found")
    return public(row)


@router.post("/commands/{command_id}/approve")
async def approve_command(
    command_id: str, session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id), principal: Principal = Depends(require_owner),
) -> dict:
    row = await remote_repo.get_for_tenant(session, tenant_id, command_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        await remote_repo.approve(
            session, row, actor=principal.label,
            require_distinct_actor=get_settings().remote_command_two_person_approval,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return public(row)


@router.post("/commands/{command_id}/cancel")
async def cancel_command(
    command_id: str, session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id), principal: Principal = Depends(require_admin),
) -> dict:
    row = await remote_repo.get_for_tenant(session, tenant_id, command_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        await remote_repo.cancel(session, row, actor=principal.label)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return public(row)


@router.get("/audit-events")
async def audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id), _: Principal = Depends(require_owner),
) -> dict:
    rows = await remote_repo.list_audit(session, tenant_id, limit=limit)
    return {"events": [{
        "id": row.id, "actor": row.actor, "action": row.action,
        "target_type": row.target_type, "target_id": row.target_id,
        "details": row.details, "created_at": row.created_at,
    } for row in rows]}
