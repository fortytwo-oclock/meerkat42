from datetime import datetime, timezone

from fastapi import APIRouter

from hub import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "service": "control-plane-hub",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
