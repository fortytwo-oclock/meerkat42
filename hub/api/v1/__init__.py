from fastapi import APIRouter

from hub.api.v1 import alerts, groups, health, licenses, metrics, nodes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(licenses.router, tags=["license"])
api_router.include_router(groups.router, tags=["groups"])
api_router.include_router(nodes.router, tags=["nodes"])
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(metrics.router, tags=["metrics"])
