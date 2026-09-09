from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hub import __version__
from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, require_owner
from hub.models import ClientGroup, Node, RemoteCommand
from hub.ws.bus import bus

router = APIRouter()


@router.get("/live")
async def live() -> dict:
    return {
        "status": "alive",
        "service": "control-plane-hub",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
@router.get("/health")
async def ready(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if not await bus.backend_ready():
        raise HTTPException(status_code=503, detail="realtime backplane unavailable")
    return {
        "status": "healthy", "service": "control-plane-hub",
        "version": __version__, "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {"database": "ok", "realtime_backplane": "ok"},
    }


@router.get("/ops/metrics", response_class=PlainTextResponse)
async def operational_metrics(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_owner),
) -> str:
    node_count = (await session.execute(
        select(func.count()).select_from(Node).join(ClientGroup).where(ClientGroup.tenant_id == tenant_id)
    )).scalar_one()
    queued = (await session.execute(
        select(func.count()).select_from(RemoteCommand).where(
            RemoteCommand.tenant_id == tenant_id,
            RemoteCommand.status.in_(("QUEUED", "AWAITING_APPROVAL", "DISPATCHED")),
        )
    )).scalar_one()
    failed = (await session.execute(
        select(func.count()).select_from(RemoteCommand).where(
            RemoteCommand.tenant_id == tenant_id,
            RemoteCommand.status.in_(("FAILED", "TIMED_OUT")),
        )
    )).scalar_one()
    return (
        "# HELP control_plane_nodes Managed nodes in the tenant.\n"
        "# TYPE control_plane_nodes gauge\n"
        f"control_plane_nodes {node_count}\n"
        "# HELP control_plane_remote_commands_pending Commands awaiting completion.\n"
        "# TYPE control_plane_remote_commands_pending gauge\n"
        f"control_plane_remote_commands_pending {queued}\n"
        "# HELP control_plane_remote_commands_failed Failed or timed-out commands.\n"
        "# TYPE control_plane_remote_commands_failed gauge\n"
        f"control_plane_remote_commands_failed {failed}\n"
    )
