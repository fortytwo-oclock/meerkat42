from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime, created_at_col, uuid_pk

__all__ = [
    "AlertMetric",
    "Comparator",
    "Severity",
    "AlertState",
    "AlertRule",
    "AlertPendingCondition",
    "AlertEvent",
    "NotificationChannel",
]


class AlertMetric(str, enum.Enum):
    CPU_USAGE_PCT = "cpu_usage_pct"
    RAM_USAGE_PCT = "ram_usage_pct"
    LOAD_1 = "load_1"
    CONTAINER_UPDATES_AVAILABLE = "container_updates_available"
    NODE_OFFLINE = "node_offline"
    CONTAINER_CRASH_LOOP = "container_crash_loop"
    ZFS_STATE = "zfs_state"
    DISK_HEALTH = "disk_health"


class Comparator(str, enum.Enum):
    GT = "gt"
    LT = "lt"
    EQ = "eq"
    NE = "ne"


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertState(str, enum.Enum):
    FIRING = "FIRING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = uuid_pk()
    # NULL group_id = applies to every node in the tenant.
    group_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("client_groups.id", ondelete="CASCADE"), default=None, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    metric: Mapped[AlertMetric] = mapped_column(String(32))
    comparator: Mapped[Comparator] = mapped_column(String(4), default=Comparator.GT)
    threshold: Mapped[Optional[float]] = mapped_column(default=None)  # None for boolean metrics
    duration_sec: Mapped[int] = mapped_column(default=0)  # sustained-for before firing
    severity: Mapped[Severity] = mapped_column(String(16), default=Severity.WARNING)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_builtin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at_col()


class AlertPendingCondition(Base):
    """Restart-safe timer for a condition that has not yet met its duration."""

    __tablename__ = "alert_pending_conditions"
    __table_args__ = (
        UniqueConstraint(
            "rule_id", "node_id", "type", "subject", name="uq_alert_pending_condition"
        ),
    )

    id: Mapped[str] = uuid_pk()
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255), default="")
    first_seen_at: Mapped[datetime] = created_at_col()


class AlertEvent(Base):
    """Stateful alert lifecycle. One FIRING event per (rule, node, subject) until resolved."""

    __tablename__ = "dispatched_alerts"

    id: Mapped[str] = uuid_pk()
    rule_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), default=None, index=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), index=True
    )
    # e.g. container name or zfs pool name, for per-subject dedupe. "" for node-level.
    subject: Mapped[str] = mapped_column(String(255), default="")

    severity: Mapped[Severity] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[Optional[float]] = mapped_column(default=None)

    state: Mapped[AlertState] = mapped_column(String(16), default=AlertState.FIRING, index=True)
    started_at: Mapped[datetime] = created_at_col()
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(200), default=None)
    # dispatch bookkeeping (Phase 3)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    resolve_notified_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    # suppressed = created inside the flap window of a recent identical alert; not notified
    suppressed: Mapped[bool] = mapped_column(default=False)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # NULL = all groups in the tenant
    group_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("client_groups.id", ondelete="CASCADE"), default=None
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # discord | slack | smtp | webhook
    config: Mapped[dict] = mapped_column(default=dict)  # url / smtp settings / recipients
    min_severity: Mapped[Severity] = mapped_column(String(16), default=Severity.WARNING)
    enabled: Mapped[bool] = mapped_column(default=True)
    muted_until: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    created_at: Mapped[datetime] = created_at_col()
    last_error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
