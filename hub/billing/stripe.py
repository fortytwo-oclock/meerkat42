from __future__ import annotations

import hashlib
import hmac
import time


class StripeWebhookError(ValueError):
    pass


def verify_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> None:
    """Verify Stripe's t=...,v1=... HMAC over the untouched request body."""
    if not secret:
        raise StripeWebhookError("Stripe webhook secret is not configured")
    values: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values["t"][0])
    except (KeyError, ValueError, IndexError) as exc:
        raise StripeWebhookError("Malformed Stripe-Signature header") from exc
    signatures = values.get("v1", [])
    if not signatures:
        raise StripeWebhookError("Stripe-Signature has no v1 signature")

    current = int(time.time()) if now is None else int(now)
    if tolerance_seconds > 0 and abs(current - timestamp) > tolerance_seconds:
        raise StripeWebhookError("Stripe signature timestamp is outside tolerance")

    signed = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, supplied) for supplied in signatures):
        raise StripeWebhookError("Stripe signature verification failed")
