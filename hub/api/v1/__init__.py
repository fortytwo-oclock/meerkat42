from fastapi import APIRouter

from hub.api.v1 import (
    alerts,
    agent_tokens,
    auth,
    branding,
    billing,
    channels,
    commands,
    emergency,
    enterprise,
    groups,
    health,
    licenses,
    mcp,
    metrics,
    migration,
    nodes,
    tokens,
    vault,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(branding.router, tags=["branding"])
api_router.include_router(billing.router, tags=["billing"])
api_router.include_router(licenses.router, tags=["license"])
api_router.include_router(groups.router, tags=["groups"])
api_router.include_router(nodes.router, tags=["nodes"])
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(agent_tokens.router, tags=["agent-tokens"])
api_router.include_router(channels.router, tags=["channels"])
api_router.include_router(commands.router, tags=["remote-commands"])
api_router.include_router(emergency.router, tags=["emergency"])
api_router.include_router(enterprise.router, tags=["enterprise"])
api_router.include_router(tokens.router, tags=["tokens"])
api_router.include_router(vault.router, tags=["vault"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(migration.router, tags=["migration-and-fleet"])
api_router.include_router(mcp.router, tags=["mcp"])
