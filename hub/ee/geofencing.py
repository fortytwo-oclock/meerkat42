"""Enterprise geofencing evaluation using Haversine great-circle distance."""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import GeofenceZone, Node
from hub.models.alert import Severity
from hub.repositories import alerts as alerts_repo

_EARTH_RADIUS_M = 6_371_000.0


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in meters between two WGS84 coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


async def evaluate_geofences(
    session: AsyncSession, tenant_id: str, node: Node
) -> list[str]:
    """Evaluate `node.last_location` against all enabled GeofenceZones for `tenant_id`.

    Returns a list of matched zone names the device is currently inside.
    """
    loc = node.last_location
    if not isinstance(loc, dict):
        return []
    lat = loc.get("lat")
    lon = loc.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return []

    rows = await session.execute(
        select(GeofenceZone).where(
            GeofenceZone.tenant_id == tenant_id,
            GeofenceZone.enabled.is_(True),
        )
    )
    zones = list(rows.scalars())
    inside_zones: list[str] = []

    for zone in zones:
        dist_m = haversine_meters(float(lat), float(lon), float(zone.lat), float(zone.lon))
        is_inside = dist_m <= float(zone.radius_m)
        severity = Severity(zone.severity) if zone.severity in Severity._value2member_map_ else Severity.WARNING

        if is_inside:
            inside_zones.append(zone.name)
            if zone.alert_on_exit:
                await alerts_repo.resolve_by_type(
                    session, node.node_id, "GEOFENCE_EXIT", subject=zone.name
                )
            if zone.alert_on_enter:
                await alerts_repo.open_or_update_event(
                    session,
                    node_id=node.node_id,
                    type_="GEOFENCE_ENTER",
                    subject=zone.name,
                    severity=severity,
                    message=f"{node.hostname} entered geofence '{zone.name}' ({dist_m:.0f}m from center)",
                    value=round(dist_m, 1),
                )
        else:
            if zone.alert_on_enter:
                await alerts_repo.resolve_by_type(
                    session, node.node_id, "GEOFENCE_ENTER", subject=zone.name
                )
            if zone.alert_on_exit:
                await alerts_repo.open_or_update_event(
                    session,
                    node_id=node.node_id,
                    type_="GEOFENCE_EXIT",
                    subject=zone.name,
                    severity=severity,
                    message=(
                        f"{node.hostname} left geofence '{zone.name}' "
                        f"({dist_m:.0f}m away, radius {zone.radius_m:.0f}m)"
                    ),
                    value=round(dist_m, 1),
                )

    return inside_zones
