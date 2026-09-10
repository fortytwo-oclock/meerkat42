from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import get_metric_store, get_tenant_id, viewer_scope
from hub.repositories import groups as groups_repo
from hub.repositories import nodes as nodes_repo
from hub.timeseries import MetricStore
from hub.timeseries.base import extract_scalars

router = APIRouter()


def _summary(node, group_slug: str) -> dict:
    t = node.last_telemetry or {}
    s = extract_scalars(t)
    return {
        "node_id": node.node_id,
        "hostname": node.hostname,
        "arch": node.arch,
        "client_group": group_slug,
        "status": node.status.value if hasattr(node.status, "value") else node.status,
        "last_seen": node.last_seen,
        "agent_version": node.agent_version,
        "cpu_usage_pct": s["cpu_usage_pct"],
        "ram_usage_pct": s["ram_usage_pct"],
        "load_1": s["load_1"],
        "containers_total": int(s["containers_total"]),
        "containers_running": int(s["containers_running"]),
        "crash_loop_count": int(s["crash_loop_count"]),
        "container_updates_available": int(s["container_updates_available"]),
        "zfs_pools_count": int(s["zfs_pools_count"]),
        "zfs_degraded_count": int(s["zfs_degraded_count"]),
    }


@router.get("/nodes")
async def list_nodes(
    client_group: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    scope: str | None = Depends(viewer_scope),
) -> dict:
    if scope:
        client_group = scope
    groups = {g.id: g.slug for g in await groups_repo.list_groups(session, tenant_id)}
    nodes = await nodes_repo.list_for_tenant(session, tenant_id, group_slug=client_group)
    results = [_summary(n, groups.get(n.group_id, "default")) for n in nodes]
    return {"nodes": results, "total": len(results)}


@router.get("/nodes/{node_id}")
async def get_node(
    node_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    scope: str | None = Depends(viewer_scope),
) -> dict:
    node = await nodes_repo.get_for_tenant(session, tenant_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    groups = {g.id: g for g in await groups_repo.list_groups(session, tenant_id)}
    group = groups.get(node.group_id)
    if scope and (group is None or group.slug != scope):
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return {
        "node_id": node.node_id,
        "hostname": node.hostname,
        "os": node.os,
        "arch": node.arch,
        "kernel": node.kernel,
        "agent_version": node.agent_version,
        "remote_ip": node.remote_ip,
        "status": node.status.value if hasattr(node.status, "value") else node.status,
        "uptime_sec": node.uptime_sec,
        "first_seen": node.first_seen,
        "last_seen": node.last_seen,
        "client_group": group.slug if group else "default",
        "labels": node.labels,
        "latest_telemetry": node.last_telemetry,
    }


@router.get("/nodes/{node_id}/metrics")
async def node_metrics(
    node_id: str,
    metric: str = Query(...),
    range_hours: int = Query(default=6, ge=1, le=24 * 30),
    session: AsyncSession = Depends(get_session),
    store: MetricStore = Depends(get_metric_store),
    tenant_id: str = Depends(get_tenant_id),
    scope: str | None = Depends(viewer_scope),
) -> dict:
    node = await nodes_repo.get_for_tenant(session, tenant_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if scope:
        groups = {g.id: g.slug for g in await groups_repo.list_groups(session, tenant_id)}
        if groups.get(node.group_id) != scope:
            raise HTTPException(status_code=404, detail="Node not found")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=range_hours)
    series = await store.query(node_id, metric, start, end)
    return {
        "node_id": node_id,
        "metric": metric,
        "resolution": series.resolution,
        "points": [{"ts": p.ts, "value": p.value} for p in series.points],
    }
