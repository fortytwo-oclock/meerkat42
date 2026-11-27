"""Enterprise OIDC SSO: discovery, PKCE state store, code exchange, and JWKS ID-token verification."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from hub.config import Settings
from hub.license_manager import LicenseManager
from hub.models.auth import Role


class OidcError(ValueError):
    """Raised on invalid OIDC state, token exchange failure, or JWT verification failure."""


class OidcStateStore:
    """Bounded, single-use PKCE state/verifier store with TTL."""

    def __init__(self, ttl_seconds: int = 600, max_entries: int = 2048) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, float]] = {}

    def put(self, state: str, code_verifier: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            if len(self._items) >= self._max:
                oldest = next(iter(self._items))
                self._items.pop(oldest, None)
            self._items[state] = (code_verifier, now + self._ttl)

    def consume(self, state: str) -> str:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            entry = self._items.pop(state, None)
        if entry is None:
            raise OidcError("Invalid, expired, or already-used OIDC state")
        verifier, exp = entry
        if now > exp:
            raise OidcError("OIDC login state has expired")
        return verifier

    def _prune_locked(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._items.items() if now > exp]
        for k in expired:
            self._items.pop(k, None)


state_store = OidcStateStore()


def _b64url_decode(val: str) -> bytes:
    pad = "=" * (-len(val) % 4)
    return base64.urlsafe_b64decode(val + pad)


def _b64url_uint(val: str) -> int:
    return int.from_bytes(_b64url_decode(val), "big")


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


def build_authorization_request(
    settings: Settings, *, store: OidcStateStore = state_store
) -> dict:
    """Generate an OIDC Authorization Code + PKCE (S256) login redirect payload and persist state."""
    if not oidc_configured(settings):
        raise OidcError(
            "OIDC is not configured (set OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_REDIRECT_URI)."
        )
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    store.put(state, code_verifier)

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


def _select_jwk(jwks: dict[str, Any], kid: str | None, alg: str) -> dict[str, Any]:
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        raise OidcError("JWKS contains no signing keys")
    if kid:
        for k in keys:
            if isinstance(k, dict) and k.get("kid") == kid:
                return k
        raise OidcError(f"Signing key kid={kid!r} not found in JWKS")
    if len(keys) == 1 and isinstance(keys[0], dict):
        return keys[0]
    for k in keys:
        if isinstance(k, dict) and (k.get("alg") == alg or k.get("use") == "sig"):
            return k
    raise OidcError("Unable to select a signing key from JWKS")


def verify_id_token_with_jwks(
    id_token: str,
    jwks: dict[str, Any],
    *,
    issuer: str,
    client_id: str,
    now: float | None = None,
    clock_skew_sec: int = 60,
) -> dict[str, Any]:
    """Cryptographically verify an OIDC ID token (RS256, ES256, or EdDSA) against a JWKS document."""
    parts = id_token.strip().split(".")
    if len(parts) != 3:
        raise OidcError("Malformed ID token")
    h_b64, p_b64, s_b64 = parts
    try:
        header = json.loads(_b64url_decode(h_b64).decode("utf-8"))
        claims = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        sig = _b64url_decode(s_b64)
    except Exception as exc:
        raise OidcError("Unable to decode ID token segments") from exc

    alg = str(header.get("alg", ""))
    if alg not in {"RS256", "ES256", "EdDSA"}:
        raise OidcError(f"Unsupported ID token signing algorithm: {alg!r}")

    jwk = _select_jwk(jwks, header.get("kid"), alg)
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")

    try:
        if alg == "RS256":
            if jwk.get("kty") != "RSA":
                raise OidcError("RS256 requires an RSA JWK")
            pub = rsa.RSAPublicNumbers(_b64url_uint(jwk["e"]), _b64url_uint(jwk["n"])).public_key()
            pub.verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
                raise OidcError("ES256 requires a P-256 EC JWK")
            if len(sig) != 64:
                raise OidcError("Invalid ES256 signature length")
            pub = ec.EllipticCurvePublicNumbers(
                _b64url_uint(jwk["x"]), _b64url_uint(jwk["y"]), ec.SECP256R1()
            ).public_key()
            r = int.from_bytes(sig[:32], "big")
            s = int.from_bytes(sig[32:], "big")
            pub.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
        elif alg == "EdDSA":
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                raise OidcError("EdDSA requires an Ed25519 OKP JWK")
            pub = ed25519.Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"]))
            pub.verify(sig, signing_input)
    except OidcError:
        raise
    except Exception as exc:
        raise OidcError("ID token signature verification failed") from exc

    ts = time.time() if now is None else now
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or ts > float(exp) + clock_skew_sec:
        raise OidcError("ID token has expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and ts + clock_skew_sec < float(nbf):
        raise OidcError("ID token is not yet valid")

    expected_iss = issuer.strip().rstrip("/")
    actual_iss = str(claims.get("iss", "")).strip().rstrip("/")
    if actual_iss != expected_iss:
        raise OidcError(f"ID token issuer mismatch: expected {expected_iss!r}")

    aud = claims.get("aud")
    aud_list = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
    if client_id not in aud_list:
        raise OidcError("ID token audience does not include configured OIDC client_id")

    if not claims.get("sub"):
        raise OidcError("ID token missing required 'sub' claim")
    return claims


def resolve_role_from_claims(claims: dict[str, Any], settings: Settings) -> Role:
    """Map IdP `groups` claim to a Meerkat42 RBAC Role (default: VIEWER)."""
    raw_groups = claims.get("groups")
    user_groups = (
        {str(g).strip().lower() for g in raw_groups if str(g).strip()}
        if isinstance(raw_groups, list)
        else set()
    )
    owner_groups = {
        g.strip().lower() for g in settings.oidc_owner_groups.split(",") if g.strip()
    }
    admin_groups = {
        g.strip().lower() for g in settings.oidc_admin_groups.split(",") if g.strip()
    }
    if user_groups & owner_groups:
        return Role.OWNER
    if user_groups & admin_groups:
        return Role.ADMIN
    return Role.VIEWER


async def exchange_code_and_verify(
    settings: Settings,
    *,
    code: str,
    state: str,
    store: OidcStateStore = state_store,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Complete OIDC PKCE callback: consume state, exchange code, fetch JWKS, verify ID token."""
    if not oidc_configured(settings):
        raise OidcError("OIDC is not configured")
    if not code.strip():
        raise OidcError("Authorization code is required")

    code_verifier = store.consume(state)
    issuer = settings.oidc_issuer_url.strip().rstrip("/")
    token_endpoint = settings.oidc_token_endpoint.strip()
    jwks_uri = settings.oidc_jwks_uri.strip()

    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        if not token_endpoint or not jwks_uri:
            disc_resp = await client.get(f"{issuer}/.well-known/openid-configuration")
            if disc_resp.status_code >= 300:
                raise OidcError(f"OIDC discovery failed (HTTP {disc_resp.status_code})")
            disc = disc_resp.json()
            token_endpoint = token_endpoint or str(disc.get("token_endpoint", "")).strip()
            jwks_uri = jwks_uri or str(disc.get("jwks_uri", "")).strip()

        if not token_endpoint or not jwks_uri:
            raise OidcError("OIDC token_endpoint or jwks_uri could not be resolved")

        form = {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "redirect_uri": settings.oidc_redirect_uri.strip(),
            "client_id": settings.oidc_client_id.strip(),
            "code_verifier": code_verifier,
        }
        if settings.oidc_client_secret.strip():
            form["client_secret"] = settings.oidc_client_secret.strip()

        tok_resp = await client.post(token_endpoint, data=form)
        if tok_resp.status_code >= 300:
            raise OidcError(f"OIDC token exchange failed (HTTP {tok_resp.status_code})")
        tok_body = tok_resp.json()
        id_token = tok_body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("OIDC token response did not include an id_token")

        jwks_resp = await client.get(jwks_uri)
        if jwks_resp.status_code >= 300:
            raise OidcError(f"Failed to fetch OIDC JWKS (HTTP {jwks_resp.status_code})")
        jwks = jwks_resp.json()

    claims = verify_id_token_with_jwks(
        id_token,
        jwks,
        issuer=issuer,
        client_id=settings.oidc_client_id.strip(),
    )
    role = resolve_role_from_claims(claims, settings)
    identity = str(claims.get("email") or claims.get("preferred_username") or claims["sub"])
    return {
        "sub": str(claims["sub"]),
        "identity": identity,
        "role": role,
        "claims": claims,
    }
