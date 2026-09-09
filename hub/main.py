"""FastAPI application factory + lifespan wiring."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from hub import __version__
from hub.api.v1 import api_router
from hub.config import get_settings
from hub.db.base import Base
from hub.db.session import engine, session_scope
from hub.license_manager import LicenseManager
from hub.models import AlertRule
from hub.repositories import alerts as alerts_repo
from hub.repositories import licenses as lic_repo
from hub.repositories import tenants as tenants_repo
from hub.services.alerting import BUILTIN_RULES
from hub.services.liveness import rollup_loop, watchdog_loop
from hub.timeseries import SqlMetricStore
from hub.ws.ingest import router as ingest_router
from hub.ws.ui import router as ui_router

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger("hub")
settings = get_settings()


async def _init_schema() -> None:
    """Alembic in production; create_all is the zero-config fallback for fresh SQLite."""
    if settings.auto_migrate:
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
            cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "migrations"))
            cfg.set_main_option("sqlalchemy.url", settings.database_url)
            await asyncio.to_thread(command.upgrade, cfg, "head")
            return
        except Exception:  # pragma: no cover
            log.warning("alembic upgrade failed; falling back to create_all", exc_info=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed(app: FastAPI) -> None:
    async with session_scope() as session:
        tenant, default_group = await tenants_repo.ensure_bootstrap(session)
        app.state.default_tenant_id = tenant.id

        # builtin alert rules
        existing = {
            r.name
            for r in (await session.execute(select(AlertRule).where(AlertRule.is_builtin.is_(True)))).scalars()
        }
        for spec in BUILTIN_RULES:
            if spec["name"] not in existing:
                await alerts_repo.create_rule(session, group_id=None, **spec)

        # active license
        record = await lic_repo.get_active(session, tenant.id)
        app.state.license_manager.load_from_record(
            record.raw_token if record else None, {}
        )
        if record is None:
            await lic_repo.set_active(
                session, tenant.id, raw_token=None,
                claims=app.state.license_manager.claims,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.license_manager = LicenseManager()
    app.state.metric_store = SqlMetricStore()
    await _init_schema()
    await _seed(app)

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(watchdog_loop(stop)),
        asyncio.create_task(rollup_loop(app.state.metric_store, stop)),
    ]
    log.info("hub %s ready (db=%s)", __version__, "sqlite" if settings.is_sqlite else "postgres")
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Distributed Control Plane Hub",
        version=__version__,
        description="Multi-tenant zero-trust telemetry hub for MSPs and homelabbers",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.include_router(ingest_router)
    app.include_router(ui_router)
    return app


app = create_app()
