"""Meerkat42 Enterprise Edition feature modules and entitlement guards.

See LICENSE-ENTERPRISE.md for commercial licensing terms. All Enterprise modules
are gated offline via Ed25519 signed licenses verified by LicenseManager.
"""
from __future__ import annotations

from fastapi import HTTPException

from hub.license_manager import ENTERPRISE_FEATURES, LicenseManager


def require_enterprise_feature(lm: LicenseManager, feature_key: str) -> None:
    """Fail closed with HTTP 402 if the active license does not unlock `feature_key`."""
    if lm.has_feature(feature_key):
        return
    meta = ENTERPRISE_FEATURES.get(feature_key) or {}
    name = meta.get("name", feature_key)
    raise HTTPException(
        status_code=402,
        detail=(
            f"Enterprise feature '{name}' ({feature_key}) is not unlocked by the active license. "
            "Apply a signed Meerkat42 Enterprise license in Settings."
        ),
    )
