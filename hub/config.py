"""Runtime configuration for the Hub.

Self-hosters get zero-config SQLite. Cloud sets DATABASE_URL to a Postgres DSN.
"""
from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # --- Persistence -------------------------------------------------------
    # SQLite (default, self-hosted) or postgresql+asyncpg://user:pass@host/db (cloud)
    database_url: str = "sqlite+aiosqlite:///./data/hub.db"
    db_echo: bool = False
    auto_migrate: bool = True  # run alembic upgrade head on startup

    # Optional cross-replica realtime backplane. Empty preserves the embedded,
    # single-process self-hosted mode.
    redis_url: str = ""
    redis_prefix: str = "control-plane"
    agent_lease_seconds: int = 45
    agent_lease_refresh_seconds: int = 15
    background_workers_enabled: bool = True

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
    # Allow the generic 'webhook' kind to target http:// and private/LAN addresses
    # (e.g. a self-hosted ntfy/gotify on the same network). discord/slack stay
    # locked to their public hosts regardless.
    webhook_allow_insecure: bool = False

    # --- Infrastructure Vault (Phase 4) -----------------------------
    vault_enabled: bool = True
    vault_max_backup_bytes: int = 5 * 1024 * 1024  # reject oversized agent uploads
    vault_backup_timeout_seconds: int = 60

    # --- Audited remote actions (Phase 8) ---------------------------
    remote_commands_enabled: bool = True
    remote_command_dispatch_interval_seconds: float = 1.0
    remote_command_timeout_seconds: int = 60
    remote_command_max_attempts: int = 5
    remote_command_max_result_bytes: int = 64 * 1024
    remote_command_two_person_approval: bool = False

    # --- Stripe billing / automated licenses (Phase 5) --------------
    stripe_webhook_secret: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_tolerance_seconds: int = 300
    stripe_webhook_max_bytes: int = 256 * 1024
    # Raw 32-byte Ed25519 private key, standard base64. Keep this only on the
    # billing service; self-hosted customer hubs contain the public key only.
    license_signing_private_key: str = ""
    # JSON object keyed by Stripe product id. Example value:
    # {"prod_enterprise":{"tier":"enterprise","max_nodes":100,
    #  "features":["*"]}}
    stripe_product_plans_json: str = "{}"
    billing_success_url: str = "http://localhost:3000/settings?checkout=success"
    billing_cancel_url: str = "http://localhost:3000/settings?checkout=canceled"
    billing_portal_return_url: str = "http://localhost:3000/settings"
    billing_email_enabled: bool = False
    billing_email_interval_seconds: float = 30.0
    billing_email_max_attempts: int = 8
    billing_email_from: str = ""
    billing_email_reply_to: str = ""
    billing_smtp_host: str = ""
    billing_smtp_port: int = 587
    billing_smtp_username: str = ""
    billing_smtp_password: str = ""
    billing_smtp_ssl: bool = False
    billing_smtp_starttls: bool = True
    billing_smtp_timeout_seconds: float = 10.0

    # --- Multi-tenancy -------------------------------------------------
    default_tenant_slug: str = "default"
    default_group_slug: str = "default"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def stripe_product_plans(self) -> dict[str, dict]:
        try:
            value = json.loads(self.stripe_product_plans_json)
        except json.JSONDecodeError as exc:
            raise ValueError("STRIPE_PRODUCT_PLANS_JSON must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("STRIPE_PRODUCT_PLANS_JSON must be a JSON object")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
