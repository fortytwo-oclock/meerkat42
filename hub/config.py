"""Runtime configuration for the Hub.

Self-hosters get zero-config SQLite. Cloud sets DATABASE_URL to a Postgres DSN.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # --- Persistence -------------------------------------------------------
    # SQLite (default, self-hosted) or postgresql+asyncpg://user:pass@host/db (cloud)
    database_url: str = "sqlite+aiosqlite:///./data/hub.db"
    db_echo: bool = False
    auto_migrate: bool = True  # run alembic upgrade head on startup

    # --- Licensing -------------------------------------------------------
    ed25519_public_key: str = "31iGBIN15cAlkXmYXYLwZTXJYR9Yngjzf187Bf+s+xA="

    # --- Auth -----------------------------------------------------------
    # Shared secret an edge agent must present (X-Auth-Token) to stream telemetry.
    # Empty = accept any agent (dev only; set this in every real deployment).
    agent_bootstrap_token: str = ""
    # Bearer/X-Admin-Token required for mutating REST endpoints (license, groups,
    # alert rules). Empty = open (single-operator self-hosted default).
    admin_token: str = ""

    # --- Liveness / watchdog -------------------------------------------
    node_stale_seconds: float = 25.0
    node_offline_seconds: float = 30.0
    watchdog_interval_seconds: float = 5.0

    # --- Time-series retention & downsampling -------------------------
    # tier resolution (s) : retention (h)   -> 10s/24h, 60s/7d, 300s/30d
    ts_raw_resolution_seconds: int = 10
    ts_raw_retention_hours: int = 24
    ts_rollup_1m_retention_hours: int = 24 * 7
    ts_rollup_5m_retention_hours: int = 24 * 30
    ts_rollup_interval_seconds: float = 60.0
    ts_prune_interval_seconds: float = 3600.0

    # --- Alert dispatch (Phase 3) ------------------------------------
    dispatch_interval_seconds: float = 10.0
    dispatch_enabled: bool = True
    # Suppress a re-fire of an identical (node,type,subject) alert if the previous
    # one resolved within this window — flapping protection.
    alert_flap_window_seconds: int = 300
    # Per-channel minimum gap between messages, anti-spam.
    channel_min_interval_seconds: int = 15
    dispatch_http_timeout_seconds: float = 10.0

    # --- Multi-tenancy -------------------------------------------------
    default_tenant_slug: str = "default"
    default_group_slug: str = "default"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
