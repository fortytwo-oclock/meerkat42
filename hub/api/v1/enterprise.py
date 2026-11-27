"""Push device registration and Enterprise API routes (Geofencing, OIDC SSO, SIEM Audit Export)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import (
    Principal,
    authenticate,
    get_license_manager,
    get_tenant_id,
    get_tenant_license_manager,
    require_admin,
    require_owner,
)
from hub.ee import require_enterprise_feature
from hub.ee.audit_export import fetch_audit_events, render_ndjson
from hub.ee.oidc import (
    OidcError,
    build_authorization_request,
    exchange_code_and_verify,
    public_sso_config,
)
from hub.license_manager import LicenseManager
from hub.models import GeofenceZone, PushDevice
from hub.repositories import remote as remote_repo
from hub.repositories import tokens as tokens_repo

router = APIRouter()

_PUSH_PLATFORMS = {"ios", "android", "macos", "windows"}
_SEVERITIES = {"INFO", "WARNING", "HIGH", "CRITICAL"}


class PushDeviceRegister(BaseModel):
    platform: str = Field(..., min_length=2, max_length=32)
    device_token: str = Field(..., min_length=8, max_length=512)
    device_name: str = Field(default="Meerkat42 App", max_length=200)


class GeofenceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    radius_m: float = Field(default=250.0, gt=5.0, le=500_000.0)
    alert_on_exit: bool = True
    alert_on_enter: bool = False
    severity: str = "WARNING"


# -- Mobile / Desktop Push Notification Devices (Community & Enterprise) ------


@router.post("/push/devices")
async def register_push_device(
    req: PushDeviceRegister,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(authenticate),
) -> dict:
    platform = req.platform.strip().lower()
    if platform not in _PUSH_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"platform must be one of {sorted(_PUSH_PLATFORMS)}",
        )
    token = req.device_token.strip()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)

    existing = (
        await session.execute(
            select(PushDevice).where(
                PushDevice.tenant_id == tenant_id,
                PushDevice.token_hash == digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.device_name = req.device_name.strip() or existing.device_name
        existing.platform = platform
        existing.principal_label = principal.label
        existing.enabled = True
        existing.last_seen_at = now
        device = existing
    else:
        device = PushDevice(
            tenant_id=tenant_id,
            principal_label=principal.label,
            platform=platform,
            device_name=req.device_name.strip() or "Meerkat42 App",
            token_hash=digest,
            device_token=token,
            enabled=True,
            last_seen_at=now,
        )
        session.add(device)
    await session.flush()
    await session.commit()
    return {
        "id": device.id,
        "platform": device.platform,
        "device_name": device.device_name,
        "principal": device.principal_label,
        "enabled": device.enabled,
    }


@router.get("/push/devices")
async def list_push_devices(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(authenticate),
) -> dict:
    from hub.models.auth import Role

    stmt = select(PushDevice).where(PushDevice.tenant_id == tenant_id)
    if not principal.can(Role.ADMIN):
        stmt = stmt.where(PushDevice.principal_label == principal.label)
    rows = await session.execute(stmt.order_by(PushDevice.last_seen_at.desc()))
    devices = [
        {
            "id": d.id,
            "platform": d.platform,
            "device_name": d.device_name,
            "principal": d.principal_label,
            "enabled": d.enabled,
            "last_seen_at": d.last_seen_at,
        }
        for d in rows.scalars()
    ]
    return {"devices": devices, "total": len(devices)}


@router.delete("/push/devices/{device_id}")
async def delete_push_device(
    device_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(authenticate),
) -> dict:
    from hub.models.auth import Role

    device = (
        await session.execute(
            select(PushDevice).where(
                PushDevice.id == device_id, PushDevice.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Push device not found")
    if not principal.can(Role.ADMIN) and device.principal_label != principal.label:
        raise HTTPException(status_code=403, detail="Cannot remove another principal's push device")
    await session.delete(device)
    await session.commit()
    return {"deleted": device_id}


# -- Enterprise Geofencing Zones (`geofencing` license feature) ---------------


@router.get("/geofences")
async def list_geofences(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    lm: LicenseManager = Depends(get_tenant_license_manager),
    _: Principal = Depends(authenticate),
) -> dict:
    require_enterprise_feature(lm, "geofencing")
    rows = await session.execute(
        select(GeofenceZone)
        .where(GeofenceZone.tenant_id == tenant_id)
        .order_by(GeofenceZone.name)
    )
    zones = [
        {
            "id": z.id,
            "name": z.name,
            "lat": z.lat,
            "lon": z.lon,
            "radius_m": z.radius_m,
            "alert_on_exit": z.alert_on_exit,
            "alert_on_enter": z.alert_on_enter,
            "severity": z.severity,
            "enabled": z.enabled,
        }
        for z in rows.scalars()
    ]
    return {"geofences": zones, "total": len(zones)}


@router.post("/geofences")
async def create_geofence(
    req: GeofenceCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    lm: LicenseManager = Depends(get_tenant_license_manager),
    principal: Principal = Depends(require_admin),
) -> dict:
    require_enterprise_feature(lm, "geofencing")
    sev = req.severity.strip().upper()
    if sev not in _SEVERITIES:
        raise HTTPException(status_code=400, detail=f"severity must be one of {sorted(_SEVERITIES)}")
    name = req.name.strip()
    existing = (
        await session.execute(
            select(GeofenceZone).where(
                GeofenceZone.tenant_id == tenant_id, GeofenceZone.name == name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Geofence '{name}' already exists")

    zone = GeofenceZone(
        tenant_id=tenant_id,
        name=name,
        lat=req.lat,
        lon=req.lon,
        radius_m=req.radius_m,
        alert_on_exit=req.alert_on_exit,
        alert_on_enter=req.alert_on_enter,
        severity=sev,
        enabled=True,
    )
    session.add(zone)
    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.label,
        action="geofence.created",
        target_type="geofence",
        target_id=name,
        details={"lat": req.lat, "lon": req.lon, "radius_m": req.radius_m},
    )
    await session.commit()
    return {
        "id": zone.id,
        "name": zone.name,
        "lat": zone.lat,
        "lon": zone.lon,
        "radius_m": zone.radius_m,
        "alert_on_exit": zone.alert_on_exit,
        "alert_on_enter": zone.alert_on_enter,
        "severity": zone.severity,
        "enabled": zone.enabled,
    }


@router.delete("/geofences/{geofence_id}")
async def delete_geofence(
    geofence_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    lm: LicenseManager = Depends(get_tenant_license_manager),
    principal: Principal = Depends(require_admin),
) -> dict:
    require_enterprise_feature(lm, "geofencing")
    zone = (
        await session.execute(
            select(GeofenceZone).where(
                GeofenceZone.id == geofence_id, GeofenceZone.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=404, detail="Geofence not found")
    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.label,
        action="geofence.deleted",
        target_type="geofence",
        target_id=zone.name,
        details={},
    )
    await session.delete(zone)
    await session.commit()
    return {"deleted": geofence_id}


# -- Enterprise OIDC SSO (`sso_oidc` license feature) -------------------------


@router.get("/sso/config")
async def get_sso_config(
    lm: LicenseManager = Depends(get_license_manager),
) -> dict:
    return public_sso_config(get_settings(), lm)


class OidcCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=2048)
    state: str = Field(..., min_length=8, max_length=512)


@router.get("/sso/login-url")
async def get_sso_login_url(
    lm: LicenseManager = Depends(get_license_manager),
) -> dict:
    require_enterprise_feature(lm, "sso_oidc")
    try:
        return build_authorization_request(get_settings())
    except (ValueError, OidcError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sso/callback")
async def sso_callback(
    req: OidcCallbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    lm: LicenseManager = Depends(get_license_manager),
) -> dict:
    """Complete OIDC PKCE flow: verify ID token via JWKS and issue a scoped Meerkat42 API token."""
    require_enterprise_feature(lm, "sso_oidc")
    try:
        verified = await exchange_code_and_verify(
            get_settings(),
            code=req.code,
            state=req.state,
        )
    except OidcError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    tenant_id = request.app.state.default_tenant_id
    identity = verified["identity"]
    role = verified["role"]
    tok, raw = await tokens_repo.create(
        session,
        tenant_id=tenant_id,
        name=f"sso:{identity}"[:200],
        role=role,
        group_id=None,
        created_by="oidc-sso",
    )
    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=identity,
        action="sso.login",
        target_type="api_token",
        target_id=tok.id,
        details={"sub": verified["sub"], "role": role.value},
    )
    await session.commit()
    return {
        "token": raw,
        "token_id": tok.id,
        "identity": identity,
        "role": role.value,
    }


# -- Enterprise SIEM Audit Export (`audit_export` license feature) ------------


@router.get("/audit/export")
async def export_audit_logs(
    format: str = Query(default="ndjson", pattern="^(ndjson|json)$"),
    limit: int = Query(default=1000, ge=1, le=10_000),
    since: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    lm: LicenseManager = Depends(get_tenant_license_manager),
    _: Principal = Depends(require_owner),
):
    require_enterprise_feature(lm, "audit_export")
    events = await fetch_audit_events(session, tenant_id, since=since, limit=limit)
    if format == "ndjson":
        return PlainTextResponse(
            content=render_ndjson(events),
            media_type="application/x-ndjson",
        )
    return {"events": events, "total": len(events)}
