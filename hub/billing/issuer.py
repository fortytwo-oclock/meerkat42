from __future__ import annotations

import base64
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


class LicenseIssuanceError(ValueError):
    pass


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def issue_license(
    private_key_b64: str,
    *,
    subject: str,
    customer_name: str,
    plan: dict[str, Any],
    expires_at: datetime,
    expected_public_key_b64: str | None = None,
    issued_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create an EdDSA JWT compatible with the hub's offline verifier."""
    try:
        key_bytes = base64.b64decode(private_key_b64, validate=True)
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
    except Exception as exc:
        raise LicenseIssuanceError("Invalid Ed25519 license signing key") from exc

    if expected_public_key_b64:
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        try:
            expected = base64.b64decode(expected_public_key_b64, validate=True)
        except Exception as exc:
            raise LicenseIssuanceError("Invalid configured Ed25519 public key") from exc
        if not hmac.compare_digest(public_bytes, expected):
            raise LicenseIssuanceError(
                "License signing key does not match ED25519_PUBLIC_KEY"
            )

    tier = str(plan.get("tier", "")).strip()
    features = plan.get("features", [])
    try:
        max_nodes = int(plan["max_nodes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseIssuanceError("Plan requires integer max_nodes") from exc
    if not tier or not isinstance(features, list):
        raise LicenseIssuanceError("Plan requires tier and features list")

    now = issued_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise LicenseIssuanceError("License expiration must be in the future")

    claims: dict[str, Any] = {
        "iss": "control-plane-saas",
        "sub": subject,
        "jti": str(uuid.uuid4()),
        "customer_name": customer_name,
        "tier": tier,
        "max_nodes": max_nodes,
        "features": [str(feature) for feature in features],
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    for name in ("white_label_name", "white_label_logo", "white_label_color"):
        if plan.get(name):
            claims[name] = plan[name]

    header = {"alg": "EdDSA", "typ": "JWT"}
    encoded_header = _b64url(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    encoded_claims = _b64url(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = _b64url(private_key.sign(signing_input))
    return f"{encoded_header}.{encoded_claims}.{signature}", claims
