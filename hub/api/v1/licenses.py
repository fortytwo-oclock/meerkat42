from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import get_license_manager, get_tenant_id, require_admin
from hub.license_manager import LicenseError, LicenseManager
from hub.repositories import licenses as lic_repo
from hub.repositories import nodes as nodes_repo
from hub.schemas import LicenseSubmit

router = APIRouter()


@router.get("/license")
async def get_license(
    session: AsyncSession = Depends(get_session),
    lm: LicenseManager = Depends(get_license_manager),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    count = await nodes_repo.count_for_tenant(session, tenant_id)
    return {
        "active_tier": lm.claims.get("tier"),
        "customer_name": lm.claims.get("customer_name"),
        "max_nodes": lm.max_nodes,
        "current_nodes_count": count,
        "features": lm.claims.get("features"),
        "expires_at": lm.claims.get("exp"),
        "is_unlimited": lm.is_unlimited,
        "is_custom_license": lm.raw_token is not None,
        "white_label_logo": lm.claims.get("white_label_logo"),
    }


@router.post("/license")
async def apply_license(
    req: LicenseSubmit,
    session: AsyncSession = Depends(get_session),
    lm: LicenseManager = Depends(get_license_manager),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    try:
        claims = lm.apply(req.license_token)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=f"License verification failed: {exc}")
    await lic_repo.set_active(
        session, tenant_id, raw_token=req.license_token, claims=claims
    )
    await session.commit()
    return {
        "success": True,
        "message": "License cryptographically verified and applied offline.",
        "claims": claims,
    }


@router.get("/license/history")
async def license_history(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    rows = await lic_repo.history(session, tenant_id)
    return {
        "records": [
            {
                "tier": r.tier,
                "customer_name": r.customer_name,
                "max_nodes": r.max_nodes,
                "applied_at": r.applied_at,
                "expires_at": r.expires_at,
                "is_active": r.is_active,
            }
            for r in rows
        ]
    }
