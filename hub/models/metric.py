from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime


class MetricPoint(Base):
    """Raw 10s-resolution telemetry sample. Pruned to `ts_raw_retention_hours`."""

    __tablename__ = "metric_points"
    __table_args__ = (
        Index("ix_metric_points_node_metric_ts", "node_id", "metric", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE")
    )
    metric: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(TZDateTime)
    value: Mapped[float] = mapped_column()


class MetricRollup(Base):
    """Downsampled aggregate. `window` is '1m' or '5m'. Holds min/max/avg per bucket."""

    __tablename__ = "metric_rollups"
    __table_args__ = (
        Index(
            "ix_metric_rollups_lookup", "node_id", "metric", "window", "bucket_start"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE")
    )
    metric: Mapped[str] = mapped_column(String(64))
    window: Mapped[str] = mapped_column(String(8))
    bucket_start: Mapped[datetime] = mapped_column(TZDateTime)
    avg_value: Mapped[float] = mapped_column()
    min_value: Mapped[float] = mapped_column()
    max_value: Mapped[float] = mapped_column()
    sample_count: Mapped[int] = mapped_column(default=0)
