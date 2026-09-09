"""Pluggable time-series metric storage.

The Hub writes telemetry through the `MetricStore` interface so that self-hosters
use the built-in relational store (zero extra infra) while a cloud deployment can
drop in VictoriaMetrics / Timescale without touching ingestion or the API.
"""
from hub.timeseries.base import (
    SCALAR_METRICS,
    MetricSample,
    MetricSeries,
    MetricStore,
    extract_scalars,
)
from hub.timeseries.sql_store import SqlMetricStore

__all__ = [
    "MetricStore",
    "SqlMetricStore",
    "MetricSample",
    "MetricSeries",
    "SCALAR_METRICS",
    "extract_scalars",
]
