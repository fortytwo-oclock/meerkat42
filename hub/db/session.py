"""Async engine + session factory. Handles the SQLite/Postgres switch in one place."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from hub.config import get_settings

_settings = get_settings()

# Ensure the SQLite data dir exists before the engine opens the file.
if _settings.is_sqlite:
    _path = _settings.database_url.split("///")[-1]
    if _path and _path not in (":memory:",):
        os.makedirs(os.path.dirname(os.path.abspath(_path)) or ".", exist_ok=True)

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_pre_ping=True,
    future=True,
)

SessionFactory = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
    """WAL + FK enforcement + sane busy timeout for concurrent self-hosted access."""
    if _settings.is_sqlite:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionFactory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """For background tasks / startup seeding."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
