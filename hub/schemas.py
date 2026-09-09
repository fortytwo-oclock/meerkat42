from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LicenseSubmit(BaseModel):
    license_token: str


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class AlertRuleCreate(BaseModel):
    name: str
    metric: str
    comparator: str = "gt"
    threshold: Optional[float] = None
    duration_sec: int = 0
    severity: str = "WARNING"
    group_slug: Optional[str] = None
    enabled: bool = True


class ChannelCreate(BaseModel):
    name: str
    kind: str  # discord | slack | smtp | webhook
    config: dict
    min_severity: str = "WARNING"
    enabled: bool = True
    group_slug: Optional[str] = None


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    min_severity: Optional[str] = None
    enabled: Optional[bool] = None


class MuteRequest(BaseModel):
    minutes: int = 60


class NodeSummary(BaseModel):
    node_id: str
    hostname: str
    client_group: str
    status: str
    last_seen: Optional[datetime]
    cpu_usage_pct: float
    ram_usage_pct: float
    load_1: float
    containers_total: int
    containers_running: int
    crash_loop_count: int
    container_updates_available: int
    zfs_pools_count: int
    zfs_degraded_count: int
