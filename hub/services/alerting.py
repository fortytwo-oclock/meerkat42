"""Evaluate alert rules against an incoming telemetry payload.

Phase 1 scope: derive FIRING / RESOLVED AlertEvents and persist them. Actual
delivery to Discord/Slack/SMTP/webhook is Phase 3 and hangs off `dispatched_at`.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import AlertRule, Node
from hub.models.alert import AlertMetric, Comparator, Severity
from hub.repositories import alerts as alerts_repo
from hub.timeseries.base import extract_scalars

# The three checks the prototype hardcoded, now expressed as builtin rules.
BUILTIN_RULES: list[dict] = [
    {
        "name": "Node offline",
        "metric": AlertMetric.NODE_OFFLINE,
        "comparator": Comparator.EQ,
        "threshold": None,
        "severity": Severity.CRITICAL,
        "is_builtin": True,
    },
    {
        "name": "Container crash loop",
        "metric": AlertMetric.CONTAINER_CRASH_LOOP,
        "comparator": Comparator.EQ,
        "threshold": None,
        "severity": Severity.HIGH,
        "is_builtin": True,
    },
    {
        "name": "ZFS pool not ONLINE",
        "metric": AlertMetric.ZFS_STATE,
        "comparator": Comparator.NE,
        "threshold": None,
        "severity": Severity.CRITICAL,
        "is_builtin": True,
    },
]


def _cmp(value: float, comparator: Comparator, threshold: float) -> bool:
    if comparator == Comparator.GT:
        return value > threshold
    if comparator == Comparator.LT:
        return value < threshold
    if comparator == Comparator.EQ:
        return value == threshold
    if comparator == Comparator.NE:
        return value != threshold
    return False


async def evaluate(
    session: AsyncSession, node: Node, telemetry: dict, rules: list[AlertRule]
) -> None:
    scalars = extract_scalars(telemetry)
    active_keys: set[tuple[str, str]] = set()

    for rule in rules:
        if not rule.enabled:
            continue
        if rule.group_id and rule.group_id != node.group_id:
            continue

        if rule.metric == AlertMetric.CONTAINER_CRASH_LOOP:
            for c in telemetry.get("containers", []):
                if c.get("is_crash_loop"):
                    key = ("CONTAINER_CRASH_LOOP", c.get("name", ""))
                    active_keys.add(key)
                    await alerts_repo.open_or_update_event(
                        session, node_id=node.node_id, type_=key[0], subject=key[1],
                        severity=rule.severity, value=float(c.get("restart_count", 0)),
                        message=f"Container '{c.get('name')}' is crash-looping: {c.get('status')}",
                        rule_id=rule.id,
                    )
        elif rule.metric == AlertMetric.ZFS_STATE:
            for p in telemetry.get("zfs", []):
                if p.get("state") not in (None, "ONLINE"):
                    key = ("ZFS_POOL_DEGRADED", p.get("pool_name", ""))
                    active_keys.add(key)
                    await alerts_repo.open_or_update_event(
                        session, node_id=node.node_id, type_=key[0], subject=key[1],
                        severity=rule.severity, value=None,
                        message=f"ZFS pool '{p.get('pool_name')}' state is {p.get('state')}",
                        rule_id=rule.id,
                    )
        elif rule.metric == AlertMetric.DISK_HEALTH:
            for d in telemetry.get("disks", []):
                if d.get("health") not in (None, "PASSED"):
                    key = ("DISK_HEALTH", d.get("device_name", ""))
                    active_keys.add(key)
                    await alerts_repo.open_or_update_event(
                        session, node_id=node.node_id, type_=key[0], subject=key[1],
                        severity=rule.severity, value=None,
                        message=f"Disk {d.get('device_name')} SMART health: {d.get('health')}",
                        rule_id=rule.id,
                    )
        elif rule.metric == AlertMetric.NODE_OFFLINE:
            continue  # handled by the liveness watchdog, not telemetry ingest
        else:
            metric_name = rule.metric.value
            if metric_name in scalars and rule.threshold is not None:
                value = scalars[metric_name]
                key = (f"THRESHOLD_{metric_name.upper()}", "")
                if _cmp(value, rule.comparator, rule.threshold):
                    active_keys.add(key)
                    await alerts_repo.open_or_update_event(
                        session, node_id=node.node_id, type_=key[0], subject=key[1],
                        severity=rule.severity, value=value,
                        message=(
                            f"{rule.name}: {metric_name}={value:.1f} "
                            f"{rule.comparator.value} {rule.threshold}"
                        ),
                        rule_id=rule.id,
                    )

    # Resolve anything previously firing for this node that no longer applies.
    # NODE_OFFLINE events are owned by the liveness watchdog, so preserve them.
    keep = active_keys | {("NODE_OFFLINE", "")}
    await alerts_repo.resolve_stale_events(session, node.node_id, keep)
