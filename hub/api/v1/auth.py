from __future__ import annotations

from fastapi import APIRouter, Depends

from hub.deps import Principal, authenticate

router = APIRouter()


@router.get("/auth/me")
async def current_principal(
    principal: Principal = Depends(authenticate),
) -> dict:
    """Return the authenticated identity needed for role-aware dashboard controls."""
    return {
        "label": principal.label,
        "role": principal.role.value,
        "tenant_id": principal.tenant_id,
        "group_id": principal.group_id,
    }
