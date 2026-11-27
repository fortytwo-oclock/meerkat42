from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime, created_at_col, utcnow, uuid_pk


class PushDevice(Base):
    """Registered mobile/desktop push notification endpoint (APNs, FCM, WNS)."""

    __tablename__ = "push_devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "token_hash", name="uq_push_device_tenant_token"),
    )

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    principal_label: Mapped[str] = mapped_column(String(200), default="owner")
    platform: Mapped[str] = mapped_column(String(32), index=True)  # ios | android | macos | windows
    device_name: Mapped[str] = mapped_column(String(200), default="Meerkat42 App")
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    device_token: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class GeofenceZone(Base):
    """Enterprise circular geofence used to evaluate tracked device check-ins."""

    __tablename__ = "geofence_zones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_geofence_tenant_name"),
    )

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    lat: Mapped[float] = mapped_column()
    lon: Mapped[float] = mapped_column()
    radius_m: Mapped[float] = mapped_column(default=250.0)
    alert_on_exit: Mapped[bool] = mapped_column(default=True)
    alert_on_enter: Mapped[bool] = mapped_column(default=False)
    severity: Mapped[str] = mapped_column(String(16), default="WARNING")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = created_at_col()
