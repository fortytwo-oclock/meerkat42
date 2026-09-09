"""Shared FastAPI dependencies and app-scoped singletons."""
from __future__ import annotations

from fastapi import Request

from hub.license_manager import LicenseManager
from hub.timeseries import MetricStore


def get_license_manager(request: Request) -> LicenseManager:
    return request.app.state.license_manager


def get_metric_store(request: Request) -> MetricStore:
    return request.app.state.metric_store


def get_tenant_id(request: Request) -> str:
    """Self-hosted: always the default tenant. Cloud: resolve from auth/subdomain here."""
    return request.app.state.default_tenant_id
