"""Shared FastAPI dependencies and app-scoped singletons."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from hub.config import get_settings
from hub.license_manager import LicenseManager
from hub.timeseries import MetricStore


def get_license_manager(request: Request) -> LicenseManager:
    return request.app.state.license_manager


def get_metric_store(request: Request) -> MetricStore:
    return request.app.state.metric_store


def get_tenant_id(request: Request) -> str:
    """Self-hosted: always the default tenant. Cloud: resolve from auth/subdomain here."""
    return request.app.state.default_tenant_id


def _principal_from(authorization: str | None, x_admin_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_admin_token


def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> str:
    """Gate for mutating endpoints.

    When ADMIN_TOKEN is unset the hub is in single-operator mode and this is a
    no-op returning "operator". When set, a matching Bearer / X-Admin-Token is
    required and its (opaque) value identifies the principal for audit fields.
    """
    expected = get_settings().admin_token
    if not expected:
        return "operator"
    presented = _principal_from(authorization, x_admin_token)
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="admin authentication required")
    return "admin"
