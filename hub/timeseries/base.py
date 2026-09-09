"""Time-series storage interface + payload flattening helpers."""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

SCALAR_METRICS: tuple[str, ...] = (
    "cpu_usage_pct",
    "ram_usage_pct",
    "used_ram_mb",
    "load_1",
    "load_5",
    "load_15",
    "containers_total",
    "containers_running",
    "crash_loop_count",
    "zfs_pools_count",
    "zfs_degraded_count",
)


@dataclass(frozen=True)
class MetricSample:
    ts: datetime
    value: float


@dataclass(frozen=True)
class MetricSeries:
    node_id: str
    metric: str
    resolution: str  # "raw" | "1m" | "5m"
    points: list[MetricSample]


def extract_scalars(telemetry: dict) -> dict[str, float]:
    """Flatten a NodeTelemetry dict into the scalar metrics we retain."""
    host = telemetry.get("host_metrics") or {}
    containers = telemetry.get("containers") or []
    zfs = telemetry.get("zfs") or []
    running = sum(1 for c in containers if c.get("state") == "running")
    crash = sum(1 for c in containers if c.get("is_crash_loop"))
    degraded = sum(1 for p in zfs if p.get("state") not in (None, "ONLINE"))
    return {
        "cpu_usage_pct": float(host.get("cpu_usage_pct", 0) or 0),
        "ram_usage_pct": float(host.get("ram_usage_pct", 0) or 0),
        "used_ram_mb": float(host.get("used_ram_mb", 0) or 0),
        "load_1": float(host.get("load_1", 0) or 0),
        "load_5": float(host.get("load_5", 0) or 0),
        "load_15": float(host.get("load_15", 0) or 0),
        "containers_total": float(len(containers)),
        "containers_running": float(running),
        "crash_loop_count": float(crash),
        "zfs_pools_count": float(len(zfs)),
        "zfs_degraded_count": float(degraded),
    }


class MetricStore(abc.ABC):
    """Storage-agnostic contract for historical telemetry."""

    @abc.abstractmethod
    async def write(self, node_id: str, ts: datetime, metrics: dict[str, float]) -> None:
        ...

    @abc.abstractmethod
    async def query(
        self,
        node_id: str,
        metric: str,
        start: datetime,
        end: datetime,
        step_seconds: int | None = None,
    ) -> MetricSeries:
        ...

    @abc.abstractmethod
    async def latest(self, node_id: str) -> dict[str, float]:
        ...

    async def rollup(self, now: datetime) -> None:
        return None

    async def prune(self, now: datetime) -> int:
        return 0
