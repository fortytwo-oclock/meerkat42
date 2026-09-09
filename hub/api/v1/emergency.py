from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, require_owner
from hub.emergency import verify_totp
from hub.repositories import emergency as emergency_repo
from hub.repositories import nodes as nodes_repo
from hub.repositories import remote as remote_repo
from hub.remote.policy import CommandPolicyError, validate_command
from hub.api.v1.commands import public

router = APIRouter()


class DiagnosticRequest(BaseModel):
    diagnostic: str


def require_enabled() -> None:
    if not get_settings().emergency_console_enabled:
        raise HTTPException(status_code=404, detail="emergency console disabled")


@router.get("/emergency/status")
async def emergency_status(_: Principal = Depends(require_owner)) -> dict:
    return {"enabled": get_settings().emergency_console_enabled}


@router.post("/emergency/sessions", status_code=status.HTTP_201_CREATED)
async def create_emergency_session(
    x_totp_code: str | None = Header(default=None, alias="X-TOTP-Code"),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    require_enabled()
    settings = get_settings()
    try:
        valid = verify_totp(settings.emergency_totp_secret, x_totp_code or "")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="emergency TOTP is not configured") from exc
    if not valid:
        raise HTTPException(status_code=401, detail="invalid TOTP code")
    ttl = max(60, min(settings.emergency_session_ttl_seconds, 900))
    row, raw = await emergency_repo.create(
        session, tenant_id=tenant_id, actor=principal.label, ttl_seconds=ttl
    )
    await remote_repo.append_audit(
        session, tenant_id=tenant_id, actor=principal.label,
        action="emergency.session_created", target_type="emergency_access_grant",
        target_id=row.id, details={"expires_at": row.expires_at.isoformat()},
    )
    await session.commit()
    return {"grant": raw, "expires_at": row.expires_at}


@router.delete("/emergency/sessions")
async def revoke_emergency_session(
    x_emergency_grant: str | None = Header(default=None, alias="X-Emergency-Grant"),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    require_enabled()
    if not x_emergency_grant or not await emergency_repo.revoke(
        session, tenant_id=tenant_id, actor=principal.label, raw=x_emergency_grant
    ):
        raise HTTPException(status_code=401, detail="invalid emergency grant")
    await session.commit()
    return {"revoked": True}


@router.post("/nodes/{node_id}/diagnostics", status_code=status.HTTP_202_ACCEPTED)
async def run_diagnostic(
    node_id: str, request: DiagnosticRequest,
    x_emergency_grant: str | None = Header(default=None, alias="X-Emergency-Grant"),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    require_enabled()
    if not x_emergency_grant or await emergency_repo.authenticate(
        session, tenant_id=tenant_id, actor=principal.label, raw=x_emergency_grant
    ) is None:
        raise HTTPException(status_code=401, detail="invalid or expired emergency grant")
    if await nodes_repo.get_for_tenant(session, tenant_id, node_id) is None:
        raise HTTPException(status_code=404, detail="Node not found")
    try:
        policy, payload = validate_command("diagnostic_run", {"diagnostic": request.diagnostic})
    except CommandPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await remote_repo.create(
        session, tenant_id=tenant_id, node_id=node_id, action="diagnostic_run",
        payload=payload, requested_by=principal.label, destructive=policy.destructive,
        approval_required=False,
    )
    await remote_repo.append_audit(
        session, tenant_id=tenant_id, actor=principal.label,
        action="emergency.diagnostic_requested", target_type="remote_command",
        target_id=row.id, details={"node_id": node_id, "diagnostic": request.diagnostic},
    )
    await session.commit()
    return public(row)
