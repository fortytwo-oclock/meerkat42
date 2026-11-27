from __future__ import annotations

import asyncio
import base64
import json
import time
import unittest
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from hub.billing.issuer import issue_license
from hub.config import get_settings
from hub.ee.geofencing import haversine_meters
from hub.main import app


class HaversineUnitTest(unittest.TestCase):
    def test_haversine_distance_accuracy(self) -> None:
        # ~0m for identical points
        self.assertLess(haversine_meters(37.7749, -122.4194, 37.7749, -122.4194), 0.01)
        # San Francisco to Oakland (~13 km)
        dist = haversine_meters(37.7749, -122.4194, 37.8044, -122.2712)
        self.assertGreater(dist, 12_000)
        self.assertLess(dist, 15_000)


class Phase5EnterpriseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._private = ed25519.Ed25519PrivateKey.generate()
        cls._private_b64 = base64.b64encode(
            cls._private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode()
        cls._public_b64 = base64.b64encode(
            cls._private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode()
        settings = get_settings()
        cls._saved = (
            settings.admin_token,
            settings.agent_bootstrap_token,
            settings.ed25519_public_key,
            settings.background_workers_enabled,
            settings.oidc_provider_name,
            settings.oidc_issuer_url,
            settings.oidc_client_id,
            settings.oidc_redirect_uri,
        )
        settings.admin_token = "phase5-admin-token"
        settings.agent_bootstrap_token = "phase5-agent-token"
        settings.ed25519_public_key = cls._public_b64
        settings.background_workers_enabled = False
        settings.oidc_provider_name = "Okta Enterprise"
        settings.oidc_issuer_url = "https://idp.example.com/oauth2/default"
        settings.oidc_client_id = "meerkat42-client"
        settings.oidc_redirect_uri = "https://monitor.example.com/api/v1/sso/callback"

        cls.headers = {"Authorization": "Bearer phase5-admin-token"}
        cls.client = TestClient(app, headers=cls.headers)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        tenant_id = app.state.default_tenant_id
        cls.client.__exit__(None, None, None)

        from sqlalchemy import delete as sql_delete
        from hub.db.session import engine, session_scope
        from hub.license_manager import COMMUNITY_CLAIMS
        from hub.models import GeofenceZone, PushDevice
        from hub.repositories import licenses as lic_repo
        from hub.repositories import nodes as nodes_repo

        async def _cleanup() -> None:
            await engine.dispose()
            async with session_scope() as session:
                node = await nodes_repo.get(session, "phone-ops-1")
                if node is not None:
                    await nodes_repo.delete(session, node)
                await session.execute(sql_delete(GeofenceZone))
                await session.execute(sql_delete(PushDevice))
                await lic_repo.set_active(
                    session,
                    tenant_id,
                    raw_token=None,
                    claims=COMMUNITY_CLAIMS,
                )
            await engine.dispose()

        asyncio.run(_cleanup())
        app.state.license_manager.reset_to_community()

        settings = get_settings()
        (
            settings.admin_token,
            settings.agent_bootstrap_token,
            settings.ed25519_public_key,
            settings.background_workers_enabled,
            settings.oidc_provider_name,
            settings.oidc_issuer_url,
            settings.oidc_client_id,
            settings.oidc_redirect_uri,
        ) = cls._saved

    def _apply_enterprise_features(self, features: list[str]) -> None:
        token, _ = issue_license(
            private_key_b64=self._private_b64,
            subject="",
            customer_name="Enterprise Test Corp",
            plan={"tier": "enterprise", "max_nodes": -1, "features": features},
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        res = self.client.post("/api/v1/license", json={"license_token": token})
        self.assertEqual(res.status_code, 200, res.text)

    def test_01_community_blocks_enterprise_features_with_402(self) -> None:
        self.assertEqual(self.client.get("/api/v1/geofences").status_code, 402)
        self.assertEqual(self.client.get("/api/v1/sso/login-url").status_code, 402)
        self.assertEqual(self.client.get("/api/v1/audit/export").status_code, 402)

        sso_cfg = self.client.get("/api/v1/sso/config").json()
        self.assertFalse(sso_cfg["licensed"])
        self.assertFalse(sso_cfg["enabled"])

    def test_02_push_device_registration_lifecycle(self) -> None:
        reg = self.client.post(
            "/api/v1/push/devices",
            json={
                "platform": "ios",
                "device_token": "apns-token-0123456789abcdef",
                "device_name": "Ops iPhone 16",
            },
        )
        self.assertEqual(reg.status_code, 200, reg.text)
        device_id = reg.json()["id"]

        listed = self.client.get("/api/v1/push/devices")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)
        self.assertEqual(listed.json()["devices"][0]["device_name"], "Ops iPhone 16")

        deleted = self.client.delete(f"/api/v1/push/devices/{device_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/push/devices").json()["total"], 0)

    def test_03_enterprise_geofencing_sso_and_siem_audit_export(self) -> None:
        self._apply_enterprise_features(["geofencing", "sso_oidc", "audit_export"])

        # 1. OIDC SSO discovery + PKCE authorization URL
        sso_cfg = self.client.get("/api/v1/sso/config").json()
        self.assertTrue(sso_cfg["licensed"])
        self.assertTrue(sso_cfg["enabled"])
        self.assertEqual(sso_cfg["provider_name"], "Okta Enterprise")

        login = self.client.get("/api/v1/sso/login-url")
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn("code_challenge_method=S256", login.json()["authorization_url"])
        self.assertIn("client_id=meerkat42-client", login.json()["authorization_url"])

        # 2. Create Enterprise geofence around SF HQ (200m radius)
        gf = self.client.post(
            "/api/v1/geofences",
            json={
                "name": "SF HQ",
                "lat": 37.7749,
                "lon": -122.4194,
                "radius_m": 200.0,
                "alert_on_exit": True,
                "severity": "HIGH",
            },
        )
        self.assertEqual(gf.status_code, 200, gf.text)

        # 3. Stream mobile device telemetry outside SF HQ -> fires GEOFENCE_EXIT, then return -> resolves
        with self.client.websocket_connect(
            "/api/v1/agent/stream",
            headers={"X-Auth-Token": "phase5-agent-token"},
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "node_id": "phone-ops-1",
                        "hostname": "ops-pixel-9",
                        "platform": "android",
                        "device_class": "mobile",
                        "arch": "arm64",
                        "agent_version": "2.0.0-flutter-store",
                    }
                )
            )
            # Outside geofence (Oakland, ~13km away)
            ws.send_text(
                json.dumps(
                    {
                        "node_id": "phone-ops-1",
                        "hostname": "ops-pixel-9",
                        "platform": "android",
                        "device_class": "mobile",
                        "host_metrics": {"cpu_usage_pct": 12.0, "ram_usage_pct": 40.0},
                        "location": {"lat": 37.8044, "lon": -122.2712, "accuracy_m": 10.0},
                    }
                )
            )
            fired = False
            for _ in range(30):
                alerts = self.client.get("/api/v1/alerts").json()["alerts"]
                if any(a["type"] == "GEOFENCE_EXIT" and a["subject"] == "SF HQ" for a in alerts):
                    fired = True
                    break
                time.sleep(0.05)
            self.assertTrue(fired, "Expected GEOFENCE_EXIT alert when device left SF HQ")

            # Return inside SF HQ -> GEOFENCE_EXIT resolves automatically
            ws.send_text(
                json.dumps(
                    {
                        "node_id": "phone-ops-1",
                        "hostname": "ops-pixel-9",
                        "platform": "android",
                        "device_class": "mobile",
                        "host_metrics": {"cpu_usage_pct": 12.0, "ram_usage_pct": 40.0},
                        "location": {"lat": 37.7750, "lon": -122.4193, "accuracy_m": 8.0},
                    }
                )
            )
            resolved = False
            for _ in range(30):
                alerts = self.client.get("/api/v1/alerts").json()["alerts"]
                if not any(a["type"] == "GEOFENCE_EXIT" and a["subject"] == "SF HQ" for a in alerts):
                    resolved = True
                    break
                time.sleep(0.05)
            self.assertTrue(resolved, "Expected GEOFENCE_EXIT alert to resolve when device returned")

        # 4. SIEM Audit Export (NDJSON and JSON) includes the geofence.created audit event
        ndjson_resp = self.client.get("/api/v1/audit/export?format=ndjson")
        self.assertEqual(ndjson_resp.status_code, 200)
        self.assertIn("application/x-ndjson", ndjson_resp.headers["content-type"])
        lines = [json.loads(line) for line in ndjson_resp.text.strip().splitlines() if line.strip()]
        self.assertTrue(any(e["action"] == "geofence.created" for e in lines))

        json_resp = self.client.get("/api/v1/audit/export?format=json")
        self.assertEqual(json_resp.status_code, 200)
        self.assertGreaterEqual(json_resp.json()["total"], 1)

    def test_04_audit_regression_push_idor_preauth_sso_redaction_and_location(self) -> None:
        from hub.models import NotificationChannel
        from hub.repositories import channels as ch_repo
        from hub.repositories.nodes import _sanitize_location

        # 1. Location sanitizer rejects bools and non-finite floats
        self.assertIsNone(_sanitize_location({"lat": True, "lon": False}))
        self.assertIsNone(_sanitize_location({"lat": float("nan"), "lon": 0.0}))
        clean = _sanitize_location({"lat": 37.7, "lon": -122.4, "accuracy_m": float("nan")})
        self.assertIsNotNone(clean)
        self.assertNotIn("accuracy_m", clean or {})

        # 2. Channel redaction masks push_relay api_key and tokens
        fake_ch = NotificationChannel(
            id="ch-1",
            tenant_id="t-1",
            name="Mobile Push",
            kind="push_relay",
            config={
                "url": "https://push.meerkat42.io/v1/send",
                "api_key": "super-secret-relay-key",
                "tokens": [{"platform": "ios", "token": "apns-secret"}],
            },
            min_severity="WARNING",
            enabled=True,
        )
        redacted = ch_repo.redact(fake_ch)
        self.assertEqual(redacted["config"]["api_key"], "***")
        self.assertEqual(redacted["config"]["tokens"], "***")

        # 3. Pre-auth client can call /api/v1/sso/login-url when sso_oidc is licensed
        self._apply_enterprise_features(["sso_oidc"])
        sso_login = self.client.get("/api/v1/sso/login-url", headers={"Authorization": ""})
        self.assertEqual(sso_login.status_code, 200, sso_login.text)
        self.assertIn("code_challenge_method=S256", sso_login.json()["authorization_url"])

        # 4. Viewer cannot delete an owner's push device (IDOR protection)
        owner_dev = self.client.post(
            "/api/v1/push/devices",
            json={
                "platform": "ios",
                "device_token": "owner-apns-token-987654321",
                "device_name": "Owner Phone",
            },
        )
        self.assertEqual(owner_dev.status_code, 200)
        owner_dev_id = owner_dev.json()["id"]

        viewer_tok = self.client.post(
            "/api/v1/tokens",
            json={"name": "audit-viewer", "role": "viewer"},
        )
        self.assertEqual(viewer_tok.status_code, 201, viewer_tok.text)
        v_headers = {"Authorization": f"Bearer {viewer_tok.json()['token']}"}

        # Viewer does not see owner's push device
        v_list = self.client.get("/api/v1/push/devices", headers=v_headers)
        self.assertEqual(v_list.status_code, 200)
        self.assertEqual(v_list.json()["total"], 0)

        # Viewer gets 403 trying to delete owner's push device
        v_del = self.client.delete(f"/api/v1/push/devices/{owner_dev_id}", headers=v_headers)
        self.assertEqual(v_del.status_code, 403)

        # Clean up so subsequent tests see a clean push_devices table
        self.assertEqual(self.client.delete(f"/api/v1/push/devices/{owner_dev_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/tokens/{viewer_tok.json()['id']}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
