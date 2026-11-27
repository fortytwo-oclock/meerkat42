from __future__ import annotations

import asyncio
import base64
import json
import os
import plistlib
import time
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from fastapi.testclient import TestClient

from hub.billing.issuer import issue_license
from hub.config import get_settings
from hub.ee.oidc import (
    OidcError,
    OidcStateStore,
    build_authorization_request,
    exchange_code_and_verify,
    verify_id_token_with_jwks,
)
from hub.ee.timescale_store import TimescaleMetricStore, select_bucket_interval
from hub.main import app
from hub.models.auth import Role


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _uint_b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8 or 1
    return _b64url(n.to_bytes(length, "big"))


class OidcAndTimescaleUnitTest(unittest.TestCase):
    def test_rs256_id_token_verification_and_pkce_state_replay_rejection(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_nums = key.public_key().public_numbers()
        jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "key-1",
                    "use": "sig",
                    "alg": "RS256",
                    "n": _uint_b64url(pub_nums.n),
                    "e": _uint_b64url(pub_nums.e),
                }
            ]
        }
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": "key-1"}
        claims = {
            "iss": "https://idp.example.com",
            "sub": "user-42",
            "aud": "meerkat-client",
            "email": "alice@example.com",
            "groups": ["sre-admins"],
            "iat": now,
            "exp": now + 300,
        }
        h_b64 = _b64url(json.dumps(header).encode())
        p_b64 = _b64url(json.dumps(claims).encode())
        sig = key.sign(f"{h_b64}.{p_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        id_token = f"{h_b64}.{p_b64}.{_b64url(sig)}"

        verified = verify_id_token_with_jwks(
            id_token,
            jwks,
            issuer="https://idp.example.com",
            client_id="meerkat-client",
        )
        self.assertEqual(verified["email"], "alice@example.com")

        # Tampered token must fail
        bad_token = f"{h_b64}.{p_b64}.{_b64url(b'bad-sig')}"
        with self.assertRaises(OidcError):
            verify_id_token_with_jwks(
                bad_token,
                jwks,
                issuer="https://idp.example.com",
                client_id="meerkat-client",
            )

        # Single-use PKCE state store prevents replay
        store = OidcStateStore(ttl_seconds=60)
        store.put("state-123", "verifier-abc")
        self.assertEqual(store.consume("state-123"), "verifier-abc")
        with self.assertRaises(OidcError):
            store.consume("state-123")

    def test_eddsa_oidc_code_exchange_end_to_end(self) -> None:
        priv = ed25519.Ed25519PrivateKey.generate()
        pub_raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        jwks = {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": "ed-1",
                    "alg": "EdDSA",
                    "x": _b64url(pub_raw),
                }
            ]
        }
        now = int(time.time())
        header = {"alg": "EdDSA", "typ": "JWT", "kid": "ed-1"}
        claims = {
            "iss": "https://idp.example.com",
            "sub": "u-99",
            "aud": "meerkat-client",
            "email": "boss@example.com",
            "groups": ["platform-owners"],
            "iat": now,
            "exp": now + 300,
        }
        h_b64 = _b64url(json.dumps(header).encode())
        p_b64 = _b64url(json.dumps(claims).encode())
        sig = priv.sign(f"{h_b64}.{p_b64}".encode())
        id_token = f"{h_b64}.{p_b64}.{_b64url(sig)}"

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(
                    200,
                    json={
                        "token_endpoint": "https://idp.example.com/token",
                        "jwks_uri": "https://idp.example.com/jwks",
                    },
                )
            if req.url.path.endswith("/token"):
                return httpx.Response(200, json={"id_token": id_token, "access_token": "at"})
            if req.url.path.endswith("/jwks"):
                return httpx.Response(200, json=jwks)
            return httpx.Response(404)

        settings = get_settings()
        saved = (
            settings.oidc_issuer_url,
            settings.oidc_client_id,
            settings.oidc_redirect_uri,
            settings.oidc_owner_groups,
        )
        try:
            settings.oidc_issuer_url = "https://idp.example.com"
            settings.oidc_client_id = "meerkat-client"
            settings.oidc_redirect_uri = "https://monitor.example.com/callback"
            settings.oidc_owner_groups = "platform-owners"

            store = OidcStateStore()
            auth = build_authorization_request(settings, store=store)
            res = asyncio.run(
                exchange_code_and_verify(
                    settings,
                    code="auth-code-1",
                    state=auth["state"],
                    store=store,
                    transport=httpx.MockTransport(handler),
                )
            )
            self.assertEqual(res["identity"], "boss@example.com")
            self.assertEqual(res["role"], Role.OWNER)
        finally:
            (
                settings.oidc_issuer_url,
                settings.oidc_client_id,
                settings.oidc_redirect_uri,
                settings.oidc_owner_groups,
            ) = saved

    def test_timescale_bucket_selection_and_sqlite_fallback(self) -> None:
        self.assertEqual(select_bucket_interval(timedelta(hours=2)), ("", "raw"))
        self.assertEqual(select_bucket_interval(timedelta(days=3)), ("1 minute", "1m"))
        self.assertEqual(select_bucket_interval(timedelta(days=20)), ("5 minutes", "5m"))
        self.assertEqual(select_bucket_interval(timedelta(days=90)), ("1 hour", "1h"))
        self.assertEqual(select_bucket_interval(timedelta(days=400)), ("1 day", "1d"))

        store = TimescaleMetricStore(retention_days=365)
        # On SQLite default test config, ensure_hypertable safely returns False
        self.assertFalse(asyncio.run(store.ensure_hypertable()))


class PushRelayAndStoreManifestsTest(unittest.TestCase):
    def test_push_relay_auth_and_dispatch(self) -> None:
        from scripts.push_relay import app as relay_app

        old_key = os.environ.get("PUSH_RELAY_API_KEY")
        os.environ["PUSH_RELAY_API_KEY"] = "relay-test-secret"
        try:
            client = TestClient(relay_app)
            self.assertEqual(client.get("/health").status_code, 200)
            unauth = client.post(
                "/v1/send",
                json={"title": "CPU High", "body": "98% on web-1", "tokens": []},
            )
            self.assertEqual(unauth.status_code, 401)
            ok = client.post(
                "/v1/send",
                headers={"Authorization": "Bearer relay-test-secret"},
                json={
                    "title": "CPU <High> & Critical",
                    "body": "98% on web-1",
                    "tokens": [
                        {"platform": "ios", "token": "apns-tok"},
                        {"platform": "android", "token": "fcm-tok"},
                        {"platform": "windows", "token": "https://wns.example.com/ch"},
                    ],
                },
            )
            self.assertEqual(ok.status_code, 200)
            # Without live cloud push secrets configured, targets are safely skipped (0 failed)
            self.assertEqual(ok.json()["failed"], 0)
            self.assertEqual(ok.json()["skipped"], 3)
        finally:
            if old_key is None:
                os.environ.pop("PUSH_RELAY_API_KEY", None)
            else:
                os.environ["PUSH_RELAY_API_KEY"] = old_key

    def test_store_plists_entitlements_and_android_manifest_are_valid(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        ios_plist = plistlib.loads(
            (repo / "apps/meerkat_app/ios/Runner/Info.plist").read_bytes()
        )
        self.assertEqual(ios_plist["CFBundleDisplayName"], "Meerkat42")
        self.assertIn("NSLocationWhenInUseUsageDescription", ios_plist)
        self.assertIn("UIBackgroundModes", ios_plist)
        self.assertIn("CFBundleURLTypes", ios_plist)

        macos_ent = plistlib.loads(
            (repo / "apps/meerkat_app/macos/Runner/Release.entitlements").read_bytes()
        )
        self.assertTrue(macos_ent["com.apple.security.app-sandbox"])
        self.assertTrue(macos_ent["com.apple.security.network.client"])

        macos_plist = plistlib.loads(
            (repo / "apps/meerkat_app/macos/Runner/Info.plist").read_bytes()
        )
        self.assertIn("NSLocationUsageDescription", macos_plist)

        manifest_root = ET.parse(
            repo / "apps/meerkat_app/android/app/src/main/AndroidManifest.xml"
        ).getroot()
        perms = [
            el.attrib.get("{http://schemas.android.com/apk/res/android}name")
            for el in manifest_root.findall("uses-permission")
        ]
        self.assertIn("android.permission.INTERNET", perms)
        self.assertIn("android.permission.POST_NOTIFICATIONS", perms)
        self.assertIn("android.permission.FOREGROUND_SERVICE", perms)


class SsoCallbackEndpointTest(unittest.TestCase):
    def test_sso_callback_issues_scoped_api_token_and_audits(self) -> None:
        private = ed25519.Ed25519PrivateKey.generate()
        priv_b64 = base64.b64encode(
            private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode()
        pub_b64 = base64.b64encode(
            private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode()
        settings = get_settings()
        saved = (
            settings.admin_token,
            settings.ed25519_public_key,
            settings.background_workers_enabled,
        )
        settings.admin_token = "sso-cb-admin"
        settings.ed25519_public_key = pub_b64
        settings.background_workers_enabled = False

        headers = {"Authorization": "Bearer sso-cb-admin"}
        token_id = None
        try:
            with TestClient(app, headers=headers) as client:
                lic, _ = issue_license(
                    private_key_b64=priv_b64,
                    subject="",
                    customer_name="SSO Corp",
                    plan={"tier": "enterprise", "max_nodes": -1, "features": ["sso_oidc"]},
                    expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                )
                self.assertEqual(
                    client.post("/api/v1/license", json={"license_token": lic}).status_code,
                    200,
                )

                async def fake_exchange(*_args, **_kwargs):
                    return {
                        "sub": "okta-user-1",
                        "identity": "ops@ssocorp.example",
                        "role": Role.ADMIN,
                        "claims": {"sub": "okta-user-1"},
                    }

                with patch("hub.api.v1.enterprise.exchange_code_and_verify", new=fake_exchange):
                    cb = client.post(
                        "/api/v1/sso/callback",
                        headers={"Authorization": ""},
                        json={"code": "ok-code", "state": "valid-state-123456"},
                    )
                self.assertEqual(cb.status_code, 200, cb.text)
                body = cb.json()
                self.assertTrue(body["token"].startswith("cpt_"))
                self.assertEqual(body["role"], "admin")
                self.assertEqual(body["identity"], "ops@ssocorp.example")
                token_id = body["token_id"]

                # Verify the newly minted SSO token authenticates against /api/v1/auth/me
                me = client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {body['token']}"},
                )
                self.assertEqual(me.status_code, 200, me.text)
                self.assertEqual(me.json()["role"], "admin")

                if token_id:
                    client.delete(f"/api/v1/tokens/{token_id}")
        finally:
            from hub.db.session import engine, session_scope
            from hub.license_manager import COMMUNITY_CLAIMS
            from hub.repositories import licenses as lic_repo

            tenant_id = app.state.default_tenant_id

            async def _reset() -> None:
                await engine.dispose()
                async with session_scope() as session:
                    await lic_repo.set_active(
                        session, tenant_id, raw_token=None, claims=COMMUNITY_CLAIMS
                    )
                await engine.dispose()

            asyncio.run(_reset())
            app.state.license_manager.reset_to_community()
            (
                settings.admin_token,
                settings.ed25519_public_key,
                settings.background_workers_enabled,
            ) = saved


if __name__ == "__main__":
    unittest.main()
