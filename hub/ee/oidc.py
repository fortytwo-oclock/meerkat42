"""Enterprise OIDC SSO configuration and PKCE authorization URL builder."""
from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

from hub.config import Settings
from hub.license_manager import LicenseManager


def oidc_configured(settings: Settings) -> bool:
    return bool(
        settings.oidc_issuer_url.strip()
        and settings.oidc_client_id.strip()
        and settings.oidc_redirect_uri.strip()
    )


def public_sso_config(settings: Settings, lm: LicenseManager) -> dict:
    """Return safe, pre-auth OIDC discovery metadata (never exposes client secrets)."""
    licensed = lm.has_feature("sso_oidc")
    configured = oidc_configured(settings)
    auth_endpoint = (
        settings.oidc_authorization_endpoint.strip()
        or f"{settings.oidc_issuer_url.strip().rstrip('/')}/authorize"
        if configured
        else None
    )
    return {
        "licensed": licensed,
        "configured": configured,
        "enabled": bool(licensed and configured),
        "provider_name": settings.oidc_provider_name.strip() or "Enterprise SSO",
        "issuer_url": settings.oidc_issuer_url.strip() if configured else None,
        "authorization_endpoint": auth_endpoint,
        "scopes": settings.oidc_scopes.strip(),
    }


def build_authorization_request(settings: Settings) -> dict:
    """Generate an OIDC Authorization Code + PKCE (S256) login redirect payload."""
    if not oidc_configured(settings):
        raise ValueError(
            "OIDC is not configured (set OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_REDIRECT_URI)."
        )
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    endpoint = (
        settings.oidc_authorization_endpoint.strip()
        or f"{settings.oidc_issuer_url.strip().rstrip('/')}/authorize"
    )
    params = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id.strip(),
            "redirect_uri": settings.oidc_redirect_uri.strip(),
            "scope": settings.oidc_scopes.strip() or "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return {
        "authorization_url": f"{endpoint}?{params}",
        "state": state,
        "code_challenge_method": "S256",
    }
