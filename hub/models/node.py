from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hub.db.base import Base, TZDateTime, created_at_col, utcnow, uuid_pk

if TYPE_CHECKING:
    from hub.models.tenant import Tenant


class NodeStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    STALE = "STALE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class DeviceClass(str, enum.Enum):
    SERVER = "server"
    WORKSTATION = "workstation"
    MOBILE = "mobile"
    IOT = "iot"
    CONTAINER_HOST = "container_host"


VALID_DEVICE_CLASSES: frozenset[str] = frozenset(dc.value for dc in DeviceClass)
VALID_PLATFORMS: frozenset[str] = frozenset(
    {"linux", "windows", "macos", "ios", "android"}
)


class ClientGroup(Base):
    """An MSP client / logical grouping of nodes (e.g. "Client Apex", "Internal Lab")."""

    __tablename__ = "client_groups"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_group_tenant_slug"),)

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    parent_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("client_groups.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = created_at_col()

    tenant: Mapped["Tenant"] = relationship(back_populates="groups")
    nodes: Mapped[list["Node"]] = relationship(back_populates="group")


class Node(Base):
    """A monitored edge agent or tracked device. `node_id` is agent-supplied and stable across reconnects."""

    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("client_groups.id", ondelete="RESTRICT"), index=True
    )

    hostname: Mapped[str] = mapped_column(String(255), default="unknown")
    os: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    platform: Mapped[Optional[str]] = mapped_column(String(32), default=None, index=True)
    device_class: Mapped[str] = mapped_column(
        String(32), default=DeviceClass.SERVER.value, server_default=DeviceClass.SERVER.value, index=True
    )
    arch: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    kernel: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    agent_version: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    remote_ip: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    status: Mapped[NodeStatus] = mapped_column(
        String(16), default=NodeStatus.UNKNOWN
    )
    uptime_sec: Mapped[int] = mapped_column(default=0)

    first_seen: Mapped[datetime] = created_at_col()
    last_seen: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)

    labels: Mapped[dict] = mapped_column(default=dict)
    hardware_info: Mapped[Optional[dict]] = mapped_column(default=None)
    last_location: Mapped[Optional[dict]] = mapped_column(default=None)
    # Denormalised latest telemetry snapshot for fast fleet-matrix reads.
    last_telemetry: Mapped[Optional[dict]] = mapped_column(default=None)

    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    group: Mapped["ClientGroup"] = relationship(back_populates="nodes")
