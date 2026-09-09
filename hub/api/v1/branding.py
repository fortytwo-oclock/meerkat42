from __future__ import annotations

from fastapi import APIRouter, Depends

from hub.deps import get_license_manager
from hub.license_manager import LicenseManager

router = APIRouter()


@router.get("/branding")
async def branding(lm: LicenseManager = Depends(get_license_manager)) -> dict:
    """Public: white-label assets driven by license claims. Consumed by the dashboard
    shell before auth so the login/landing chrome is branded."""
    claims = lm.claims
    white_label = lm.has_feature("white_label")
    return {
        "white_label": white_label,
        "product_name": (
            claims.get("white_label_name") if white_label else None
        ) or "Control Plane",
        "logo_url": claims.get("white_label_logo") if white_label else None,
        "primary_color": claims.get("white_label_color") if white_label else None,
        "customer_name": claims.get("customer_name"),
        "tier": claims.get("tier"),
    }
