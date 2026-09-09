from __future__ import annotations

from fastapi import APIRouter, Depends

from hub.deps import get_metric_store
from hub.timeseries import SCALAR_METRICS, MetricStore

router = APIRouter()


@router.get("/metrics/catalog")
async def metrics_catalog() -> dict:
    return {"scalar_metrics": list(SCALAR_METRICS)}


@router.get("/metrics/{node_id}/latest")
async def latest(node_id: str, store: MetricStore = Depends(get_metric_store)) -> dict:
    return {"node_id": node_id, "metrics": await store.latest(node_id)}
