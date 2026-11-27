"""Default relational MetricStore: raw table + 1m/5m rollup tables, retention pruning.

Range -> resolution selection:
  <=  6h  : raw (10s)
  <=  7d  : 1m rollup
  else    : 5m rollup
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from hub.config import get_settings
from hub.db.session import session_scope
from hub.models.metric import MetricPoint, MetricRollup
from hub.timeseries.base import MetricSample, MetricSeries, MetricStore

_settings = get_settings()


def _floor(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


class SqlMetricStore(MetricStore):
    async def write(self, node_id: str, ts: datetime, metrics: dict[str, float]) -> None:
        async with session_scope() as s:
            s.add_all(
                [
                    MetricPoint(node_id=node_id, metric=m, ts=ts, value=float(v))
                    for m, v in metrics.items()
                ]
            )

    async def latest(self, node_id: str) -> dict[str, float]:
        async with session_scope() as s:
            sub = (
                select(
                    MetricPoint.metric,
                    func.max(MetricPoint.ts).label("mx"),
                )
                .where(MetricPoint.node_id == node_id)
                .group_by(MetricPoint.metric)
                .subquery()
            )
            rows = await s.execute(
                select(MetricPoint.metric, MetricPoint.value)
                .where(MetricPoint.node_id == node_id)
                .join(
                    sub,
                    (MetricPoint.metric == sub.c.metric)
                    & (MetricPoint.ts == sub.c.mx),
                )
            )
            return {m: v for m, v in rows.all()}

    async def list_metrics(self, node_id: str) -> list[str]:
        async with session_scope() as s:
            raw_rows = await s.execute(
                select(MetricPoint.metric)
                .where(MetricPoint.node_id == node_id)
                .distinct()
            )
            rollup_rows = await s.execute(
                select(MetricRollup.metric)
                .where(MetricRollup.node_id == node_id)
                .distinct()
            )
            discovered = {r[0] for r in raw_rows.all()} | {r[0] for r in rollup_rows.all()}
            return sorted(discovered)

    async def query(
        self,
        node_id: str,
        metric: str,
        start: datetime,
        end: datetime,
        step_seconds: int | None = None,
    ) -> MetricSeries:
        span = end - start
        async with session_scope() as s:
            if span <= timedelta(hours=6):
                rows = await s.execute(
                    select(MetricPoint.ts, MetricPoint.value)
                    .where(
                        MetricPoint.node_id == node_id,
                        MetricPoint.metric == metric,
                        MetricPoint.ts >= start,
                        MetricPoint.ts <= end,
                    )
                    .order_by(MetricPoint.ts)
                )
                return MetricSeries(
                    node_id, metric, "raw",
                    [MetricSample(ts, v) for ts, v in rows.all()],
                )

            window = "1m" if span <= timedelta(days=7) else "5m"
            rows = await s.execute(
                select(MetricRollup.bucket_start, MetricRollup.avg_value)
                .where(
                    MetricRollup.node_id == node_id,
                    MetricRollup.metric == metric,
                    MetricRollup.window == window,
                    MetricRollup.bucket_start >= start,
                    MetricRollup.bucket_start <= end,
                )
                .order_by(MetricRollup.bucket_start)
            )
            return MetricSeries(
                node_id, metric, window,
                [MetricSample(ts, v) for ts, v in rows.all()],
            )

    async def rollup(self, now: datetime) -> None:
        """Aggregate recent raw points into 1m/5m buckets. Portable: bucketing in Python.

        Idempotent — re-running over the same span upserts identical values.
        """
        for window, seconds in (("1m", 60), ("5m", 300)):
            end = _floor(now, seconds)
            start = end - timedelta(seconds=seconds * 5)
            async with session_scope() as s:
                rows = (
                    await s.execute(
                        select(
                            MetricPoint.node_id,
                            MetricPoint.metric,
                            MetricPoint.ts,
                            MetricPoint.value,
                        ).where(MetricPoint.ts >= start, MetricPoint.ts < end)
                    )
                ).all()

                buckets: dict[tuple[str, str, datetime], list[float]] = {}
                for node_id, metric, ts, value in rows:
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    key = (node_id, metric, _floor(ts, seconds))
                    buckets.setdefault(key, []).append(value)

                for (node_id, metric, bucket_start), values in buckets.items():
                    existing = (
                        await s.execute(
                            select(MetricRollup).where(
                                MetricRollup.node_id == node_id,
                                MetricRollup.metric == metric,
                                MetricRollup.window == window,
                                MetricRollup.bucket_start == bucket_start,
                            )
                        )
                    ).scalar_one_or_none()
                    avg_v = sum(values) / len(values)
                    if existing:
                        existing.avg_value = avg_v
                        existing.min_value = min(values)
                        existing.max_value = max(values)
                        existing.sample_count = len(values)
                    else:
                        s.add(
                            MetricRollup(
                                node_id=node_id, metric=metric, window=window,
                                bucket_start=bucket_start, avg_value=avg_v,
                                min_value=min(values), max_value=max(values),
                                sample_count=len(values),
                            )
                        )

    async def prune(self, now: datetime) -> int:
        removed = 0
        raw_cutoff = now - timedelta(hours=_settings.ts_raw_retention_hours)
        m1_cutoff = now - timedelta(hours=_settings.ts_rollup_1m_retention_hours)
        m5_cutoff = now - timedelta(hours=_settings.ts_rollup_5m_retention_hours)
        async with session_scope() as s:
            r = await s.execute(delete(MetricPoint).where(MetricPoint.ts < raw_cutoff))
            removed += r.rowcount or 0
            r = await s.execute(
                delete(MetricRollup).where(
                    MetricRollup.window == "1m", MetricRollup.bucket_start < m1_cutoff
                )
            )
            removed += r.rowcount or 0
            r = await s.execute(
                delete(MetricRollup).where(
                    MetricRollup.window == "5m", MetricRollup.bucket_start < m5_cutoff
                )
            )
            removed += r.rowcount or 0
        return removed
