from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import get_metric_store, get_tenant_id
from hub.repositories import nodes as nodes_repo
from hub.timeseries import SCALAR_METRICS, MetricStore

router = APIRouter()


@router.get("/metrics/catalog")
async def metrics_catalog() -> dict:
    return {"scalar_metrics": list(SCALAR_METRICS)}


@router.get("/metrics/{node_id}/latest")
async def latest(
    node_id: str,
    store: MetricStore = Depends(get_metric_store),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    if not await nodes_repo.get_for_tenant(session, tenant_id, node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node_id": node_id, "metrics": await store.latest(node_id)}
