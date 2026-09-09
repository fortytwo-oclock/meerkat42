from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def _secret(value: str) -> bytes:
    normalized = "".join(value.upper().split())
    if len(normalized) < 16:
        raise ValueError("EMERGENCY_TOTP_SECRET must contain at least 16 base32 characters")
    try:
        return base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    except Exception as exc:
        raise ValueError("EMERGENCY_TOTP_SECRET must be valid base32") from exc


def code_at(secret: str, counter: int) -> str:
    digest = hmac.new(_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, presented: str, *, timestamp: int | None = None) -> bool:
    if len(presented) != 6 or not presented.isdigit():
        return False
    counter = int(timestamp if timestamp is not None else time.time()) // 30
    return any(hmac.compare_digest(code_at(secret, counter + drift), presented) for drift in (-1, 0, 1))
