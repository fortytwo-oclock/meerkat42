"""Time-series storage interface + payload flattening helpers."""
from __future__ import annotations

import abc
import math
import re
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
    "container_updates_available",
    "zfs_pools_count",
    "zfs_degraded_count",
)

# Namespaced/tagged dynamic metrics reported by agents (e.g. net.rx_bps:eth0,
# disk.read_iops:nvme0n1, battery.pct, thermal.cpu_c).
DYNAMIC_METRIC_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
MAX_DYNAMIC_METRICS_PER_FRAME = 128


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


def _finite_float(raw: object) -> float | None:
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        val = float(raw)
        if math.isfinite(val):
            return val
    return None


def extract_scalars(telemetry: dict) -> dict[str, float]:
    """Flatten a NodeTelemetry dict into core and dynamic scalar metrics we retain."""
    host = telemetry.get("host_metrics") or {}
    containers = telemetry.get("containers") or []
    zfs = telemetry.get("zfs") or []
    running = sum(1 for c in containers if c.get("state") == "running")
    crash = sum(1 for c in containers if c.get("is_crash_loop"))
    updates = sum(1 for c in containers if c.get("update_available"))
    degraded = sum(1 for p in zfs if p.get("state") not in (None, "ONLINE"))
    scalars: dict[str, float] = {
        "cpu_usage_pct": float(host.get("cpu_usage_pct", 0) or 0),
        "ram_usage_pct": float(host.get("ram_usage_pct", 0) or 0),
        "used_ram_mb": float(host.get("used_ram_mb", 0) or 0),
        "load_1": float(host.get("load_1", 0) or 0),
        "load_5": float(host.get("load_5", 0) or 0),
        "load_15": float(host.get("load_15", 0) or 0),
        "containers_total": float(len(containers)),
        "containers_running": float(running),
        "crash_loop_count": float(crash),
        "container_updates_available": float(updates),
        "zfs_pools_count": float(len(zfs)),
        "zfs_degraded_count": float(degraded),
    }

    # Optional structured workstation / mobile / deep-host blocks
    battery = telemetry.get("battery")
    if isinstance(battery, dict):
        if (val := _finite_float(battery.get("pct"))) is not None:
            scalars["battery.pct"] = val
        if (val := _finite_float(battery.get("charging"))) is not None:
            scalars["battery.charging"] = val
        if (val := _finite_float(battery.get("temperature_c"))) is not None:
            scalars["battery.temp_c"] = val

    network = telemetry.get("network")
    if isinstance(network, dict):
        if (val := _finite_float(network.get("rx_bps"))) is not None:
            scalars["net.rx_bps"] = val
        if (val := _finite_float(network.get("tx_bps"))) is not None:
            scalars["net.tx_bps"] = val

    disk_io = telemetry.get("disk_io")
    if isinstance(disk_io, dict):
        if (val := _finite_float(disk_io.get("read_bps"))) is not None:
            scalars["disk.read_bps"] = val
        if (val := _finite_float(disk_io.get("write_bps"))) is not None:
            scalars["disk.write_bps"] = val

    # Arbitrary Netdata-style namespaced/tagged metrics reported in `metrics`
    dynamic = telemetry.get("metrics")
    if isinstance(dynamic, dict):
        added = 0
        for raw_key, raw_val in dynamic.items():
            if added >= MAX_DYNAMIC_METRICS_PER_FRAME:
                break
            if not isinstance(raw_key, str) or not DYNAMIC_METRIC_KEY_RE.fullmatch(raw_key):
                continue
            val = _finite_float(raw_val)
            if val is None:
                continue
            scalars[raw_key] = val
            added += 1

    return scalars


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

    async def list_metrics(self, node_id: str) -> list[str]:
        return list(SCALAR_METRICS)

    async def rollup(self, now: datetime) -> None:
        return None

    async def prune(self, now: datetime) -> int:
        return 0
