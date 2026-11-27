"""Enterprise TimescaleDB MetricStore adapter for high-scale, multi-year retention.

Uses PostgreSQL + TimescaleDB hypertables and `time_bucket()` downsampling when
`METRIC_STORE=timescale` is enabled on PostgreSQL and the `long_retention`
Enterprise license feature is unlocked. Falls back safely to `SqlMetricStore`
on SQLite or Community deployments.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, text

from hub.config import get_settings
from hub.db.session import session_scope
from hub.models.metric import MetricPoint, MetricRollup
from hub.timeseries.base import MetricSample, MetricSeries
from hub.timeseries.sql_store import SqlMetricStore

log = logging.getLogger("hub.ee.timescale")


def select_bucket_interval(span: timedelta, step_seconds: int | None = None) -> tuple[str, str]:
    """Choose a TimescaleDB `time_bucket` interval and resolution label for a query span."""
    if step_seconds is not None and step_seconds >= 60:
        return f"{int(step_seconds)} seconds", f"{int(step_seconds)}s"
    if span <= timedelta(hours=6):
        return "", "raw"
    if span <= timedelta(days=7):
        return "1 minute", "1m"
    if span <= timedelta(days=30):
        return "5 minutes", "5m"
    if span <= timedelta(days=180):
        return "1 hour", "1h"
    return "1 day", "1d"


class TimescaleMetricStore(SqlMetricStore):
    """TimescaleDB-backed MetricStore supporting multi-year Enterprise retention."""

    def __init__(self, *, retention_days: int = 365) -> None:
        self.retention_days = max(30, int(retention_days))
        self._hypertable_ready = False

    async def ensure_hypertable(self) -> bool:
        """Idempotently convert `metric_points` into a TimescaleDB hypertable on PostgreSQL."""
        settings = get_settings()
        if settings.is_sqlite:
            return False
        try:
            async with session_scope() as s:
                await s.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
                await s.execute(
                    text(
                        "SELECT create_hypertable('metric_points', 'ts', "
                        "if_not_exists => TRUE, migrate_data => TRUE)"
                    )
                )
            self._hypertable_ready = True
            log.info(
                "TimescaleDB hypertable active on metric_points (retention=%dd)",
                self.retention_days,
            )
            return True
        except Exception:
            log.warning(
                "TimescaleDB extension unavailable; falling back to standard SQL queries",
                exc_info=True,
            )
            self._hypertable_ready = False
            return False

    async def query(
        self,
        node_id: str,
        metric: str,
        start: datetime,
        end: datetime,
        step_seconds: int | None = None,
    ) -> MetricSeries:
        settings = get_settings()
        interval, label = select_bucket_interval(end - start, step_seconds=step_seconds)
        if settings.is_sqlite or not self._hypertable_ready or not interval:
            return await super().query(node_id, metric, start, end, step_seconds=step_seconds)

        sql = text(
            "SELECT time_bucket(CAST(:interval AS interval), ts) AS bucket, "
            "AVG(value) AS avg_val "
            "FROM metric_points "
            "WHERE node_id = :node_id AND metric = :metric "
            "AND ts >= :start AND ts <= :end "
            "GROUP BY bucket ORDER BY bucket"
        )
        async with session_scope() as s:
            rows = (
                await s.execute(
                    sql,
                    {
                        "interval": interval,
                        "node_id": node_id,
                        "metric": metric,
                        "start": start,
                        "end": end,
                    },
                )
            ).all()
        points = [
            MetricSample(
                ts=ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc),
                value=float(val),
            )
            for ts, val in rows
        ]
        return MetricSeries(node_id, metric, label, points)

    async def prune(self, now: datetime) -> int:
        """Retain Enterprise raw/hypertable data for `retention_days`."""
        settings = get_settings()
        if settings.is_sqlite or not self._hypertable_ready:
            return await super().prune(now)
        cutoff = now - timedelta(days=self.retention_days)
        removed = 0
        async with session_scope() as s:
            r = await s.execute(delete(MetricPoint).where(MetricPoint.ts < cutoff))
            removed += r.rowcount or 0
            r = await s.execute(delete(MetricRollup).where(MetricRollup.bucket_start < cutoff))
            removed += r.rowcount or 0
        return removed
