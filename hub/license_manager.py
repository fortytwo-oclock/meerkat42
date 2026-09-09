"""Offline Ed25519 license verification.

Signature math is unchanged from the prototype. What's new: the active license is
loaded from / persisted to the DB, and the stored raw token is *re-verified* on
load so a tampered DB row cannot escalate tier or node limits.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

from hub.config import get_settings

COMMUNITY_CLAIMS: dict[str, Any] = {
    "tier": "community",
    "customer_name": "Community Edition User",
    "max_nodes": 3,
    "features": ["docker_stats", "host_metrics", "basic_alerts"],
    "exp": 0,
}


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


class LicenseError(ValueError):
    pass


class LicenseManager:
    def __init__(self, public_key_b64: str | None = None):
        self.public_key_b64 = public_key_b64 or get_settings().ed25519_public_key
        self.raw_token: str | None = None
        self.claims: dict[str, Any] = dict(COMMUNITY_CLAIMS)

    # -- verification --------------------------------------------------
    def verify(self, token: str) -> dict[str, Any]:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(
            base64.b64decode(self.public_key_b64)
        )
        parts = token.strip().split(".")
        if len(parts) != 3:
            raise LicenseError("Malformed token: expected header.payload.signature")
        h_b64, p_b64, sig_b64 = parts
        try:
            pub.verify(_b64url_decode(sig_b64), f"{h_b64}.{p_b64}".encode())
        except Exception as exc:  # InvalidSignature
            raise LicenseError("signature verification failed: license tampered or invalid") from exc

        payload = json.loads(_b64url_decode(p_b64).decode())
        exp = payload.get("exp", 0)
        if exp and int(time.time()) > int(exp):
            raise LicenseError("license has expired")
        return payload

    # -- state --------------------------------------------------------
    def apply(self, token: str) -> dict[str, Any]:
        self.claims = self.verify(token)
        self.raw_token = token
        return self.claims

    def load_from_record(self, raw_token: str | None, fallback_claims: dict[str, Any]) -> None:
        """Called on startup with the persisted active license."""
        if not raw_token:
            self.raw_token = None
            self.claims = dict(COMMUNITY_CLAIMS)
            return
        try:
            self.claims = self.verify(raw_token)
            self.raw_token = raw_token
        except LicenseError:
            # Stored token no longer valid (expired / tampered) -> fall back safely.
            self.raw_token = None
            self.claims = dict(COMMUNITY_CLAIMS)

    def reset_to_community(self) -> None:
        self.raw_token = None
        self.claims = dict(COMMUNITY_CLAIMS)

    # -- policy -----------------------------------------------------
    @property
    def max_nodes(self) -> int:
        return int(self.claims.get("max_nodes", 3))

    @property
    def is_unlimited(self) -> bool:
        return self.max_nodes == -1

    def can_register_node(self, current_count: int, node_id: str, existing_ids: set[str]) -> bool:
        if node_id in existing_ids:
            return True
        return self.is_unlimited or current_count < self.max_nodes

    def has_feature(self, feature: str) -> bool:
        feats = self.claims.get("features", [])
        return feature in feats or "*" in feats
