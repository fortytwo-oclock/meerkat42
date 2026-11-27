"""Tenant subdomain resolution and routing metadata helpers."""
from __future__ import annotations

import re

from hub.config import Settings

_TENANT_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,46}[a-z0-9])?$")


def reserved_subdomains(settings: Settings) -> frozenset[str]:
    return frozenset(
        part.strip().lower()
        for part in settings.cloud_reserved_subdomains.split(",")
        if part.strip()
    )


def validate_tenant_slug(slug: str, settings: Settings) -> str:
    """Validate and normalize a tenant slug for safe subdomain routing."""
    normalized = slug.strip().lower()
    if not _TENANT_SLUG_RE.fullmatch(normalized):
        raise ValueError(
            "Slug must be 3-48 characters, lowercase letters/numbers/hyphens, "
            "and start and end with a letter or number."
        )
    if normalized in reserved_subdomains(settings):
        raise ValueError(f"Subdomain '{normalized}' is reserved.")
    return normalized


def extract_subdomain_slug(host: str | None, settings: Settings) -> str | None:
    """Extract `<slug>` from `<slug>.<cloud_base_domain>` when configured."""
    base = settings.cloud_base_domain.strip().lower().strip(".")
    if not base or not host:
        return None
    hostname = host.split(":", 1)[0].strip().lower().rstrip(".")
    suffix = f".{base}"
    if not hostname.endswith(suffix):
        return None
    candidate = hostname[: -len(suffix)]
    if not candidate or "." in candidate:
        return None
    if candidate in reserved_subdomains(settings):
        return None
    if not _TENANT_SLUG_RE.fullmatch(candidate):
        return None
    return candidate


def build_routing_info(
    tenant_slug: str, tenant_name: str, settings: Settings, request_base_url: str = ""
) -> dict:
    base = settings.cloud_base_domain.strip().lower().strip(".")
    is_cloud = bool(base)
    vanity_url = (
        f"https://{tenant_slug}.{base}"
        if is_cloud and tenant_slug not in reserved_subdomains(settings)
        else None
    )
    active_origin = (
        vanity_url
        or (settings.cloud_primary_url.rstrip("/") if is_cloud else request_base_url.rstrip("/"))
    )
    return {
        "tenant_slug": tenant_slug,
        "tenant_name": tenant_name,
        "mode": "cloud" if is_cloud else "self_hosted",
        "cloud_primary_url": settings.cloud_primary_url.rstrip("/"),
        "cloud_base_domain": base or None,
        "vanity_subdomain_url": vanity_url,
        "mcp_endpoint": f"{active_origin}/api/v1/mcp" if active_origin else "/api/v1/mcp",
        "export_endpoint": "/api/v1/tenant/export",
        "import_endpoint": "/api/v1/tenant/import",
    }
