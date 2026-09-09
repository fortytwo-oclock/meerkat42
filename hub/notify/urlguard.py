"""SSRF guard for outbound webhook/chat notification URLs.

Applied both when a channel is created/updated and again immediately before each
dispatch (DNS can change between the two).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from hub.config import get_settings
from hub.notify.base import ChannelError

# Hosts each provider is actually allowed to reach.
_ALLOWLIST: dict[str, set[str]] = {
    "discord": {
        "discord.com",
        "discordapp.com",
        "ptb.discord.com",
        "canary.discord.com",
    },
    "slack": {"hooks.slack.com"},
}


def _is_forbidden_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_webhook_url(url: str, kind: str) -> str:
    if not url or not isinstance(url, str):
        raise ChannelError("channel 'url' is required")
    p = urlparse(url)
    allow = _ALLOWLIST.get(kind)
    # The escape hatch only applies to the generic 'webhook' kind.
    lax = kind == "webhook" and get_settings().webhook_allow_insecure

    if p.scheme not in ("https", "http"):
        raise ChannelError("webhook URL must be http(s)")
    if p.scheme != "https" and not lax:
        raise ChannelError("webhook URL must use https")
    host = (p.hostname or "").lower()
    if not host:
        raise ChannelError("webhook URL has no host")

    if allow is not None and host not in allow:
        raise ChannelError(f"{kind} URL host must be one of {sorted(allow)}")

    try:
        infos = socket.getaddrinfo(host, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ChannelError(f"cannot resolve webhook host: {exc}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_forbidden_ip(ip) and not lax:
            raise ChannelError("webhook URL resolves to a disallowed address")

    return url
