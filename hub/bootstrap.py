"""One-shot database migration and seed command for multi-replica deployments."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI

from hub.license_manager import LicenseManager
from hub.main import _init_schema, _seed
from hub.timeseries import SqlMetricStore


async def bootstrap() -> None:
    app = FastAPI()
    app.state.license_manager = LicenseManager()
    app.state.metric_store = SqlMetricStore()
    await _init_schema()
    await _seed(app)


if __name__ == "__main__":
    asyncio.run(bootstrap())
