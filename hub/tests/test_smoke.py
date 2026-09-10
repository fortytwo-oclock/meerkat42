from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import unittest
import uuid

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from hub.config import get_settings
from hub.main import app


class HubSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        private = ed25519.Ed25519PrivateKey.generate()
        settings = get_settings()
        settings.license_signing_private_key = base64.b64encode(
            private.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        ).decode()
        settings.ed25519_public_key = base64.b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode()
        settings.stripe_webhook_secret = "whsec_test"
        settings.admin_token = "test-owner-token"
        settings.stripe_product_plans_json = json.dumps(
            {
                "prod_enterprise": {
                    "tier": "enterprise",
                    "max_nodes": 100,
                    "features": ["vault_backup", "white_label"],
                }
            }
        )
        cls.client = TestClient(app, headers={"X-Admin-Token": "test-owner-token"})
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def test_health(self) -> None:
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cross-Origin-Opener-Policy"], "same-origin")

    def test_agent_enrollment_token_is_shown_once_and_revocable(self) -> None:
        created = self.client.post(
            "/api/v1/agent-tokens",
            json={"name": "test agent", "group_slug": "default", "expires_in_days": 30},
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertTrue(body["token"].startswith("cpa_"))
        listed = self.client.get("/api/v1/agent-tokens")
        self.assertEqual(listed.status_code, 200, listed.text)
        match = next(row for row in listed.json()["tokens"] if row["id"] == body["id"])
        self.assertNotIn("token", match)
        revoked = self.client.delete(f"/api/v1/agent-tokens/{body['id']}")
        self.assertEqual(revoked.status_code, 200, revoked.text)

    def test_stripe_subscription_issues_license_idempotently(self) -> None:
        timestamp = int(time.time())
        event = {
            "id": f"evt_{uuid.uuid4().hex}",
            "type": "customer.subscription.created",
            "created": timestamp,
            "livemode": False,
            "data": {
                "object": {
                    "id": "sub_example",
                    "customer": "cus_example",
                    "status": "active",
                    "current_period_end": timestamp + 86400,
                    "cancel_at_period_end": False,
                    "metadata": {
                        "tenant_slug": "default",
                        "customer_name": "Example MSP",
                        "customer_email": "owner@example.test",
                    },
                    "items": {
                        "data": [
                            {"price": {"product": "prod_enterprise"}}
                        ]
                    },
                }
            },
        }
        payload = json.dumps(event, separators=(",", ":")).encode()
        signature = hmac.new(
            b"whsec_test",
            str(timestamp).encode() + b"." + payload,
            hashlib.sha256,
        ).hexdigest()
        headers = {"Stripe-Signature": f"t={timestamp},v1={signature}"}

        response = self.client.post(
            "/api/v1/billing/stripe/webhook", content=payload, headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["license_issued"])

        duplicate = self.client.post(
            "/api/v1/billing/stripe/webhook", content=payload, headers=headers
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["duplicate"])

        unchanged_event = json.loads(json.dumps(event))
        unchanged_event["id"] = f"evt_{uuid.uuid4().hex}"
        unchanged_event["type"] = "customer.subscription.updated"
        unchanged_event["created"] = timestamp + 1
        unchanged_payload = json.dumps(
            unchanged_event, separators=(",", ":")
        ).encode()
        unchanged_digest = hmac.new(
            b"whsec_test",
            str(timestamp).encode() + b"." + unchanged_payload,
            hashlib.sha256,
        ).hexdigest()
        unchanged_response = self.client.post(
            "/api/v1/billing/stripe/webhook",
            content=unchanged_payload,
            headers={"Stripe-Signature": f"t={timestamp},v1={unchanged_digest}"},
        )
        self.assertEqual(unchanged_response.status_code, 200, unchanged_response.text)
        self.assertFalse(unchanged_response.json()["license_issued"])

        stale_event = json.loads(json.dumps(event))
        stale_event["id"] = f"evt_{uuid.uuid4().hex}"
        stale_event["type"] = "customer.subscription.deleted"
        stale_event["created"] = timestamp - 1
        stale_event["data"]["object"]["status"] = "canceled"
        stale_payload = json.dumps(stale_event, separators=(",", ":")).encode()
        stale_digest = hmac.new(
            b"whsec_test",
            str(timestamp).encode() + b"." + stale_payload,
            hashlib.sha256,
        ).hexdigest()
        stale_response = self.client.post(
            "/api/v1/billing/stripe/webhook",
            content=stale_payload,
            headers={"Stripe-Signature": f"t={timestamp},v1={stale_digest}"},
        )
        self.assertEqual(stale_response.status_code, 200, stale_response.text)
        subscription = self.client.get("/api/v1/billing/subscription")
        self.assertEqual(subscription.status_code, 200)
        self.assertEqual(subscription.json()["status"], "active")

        download = self.client.get("/api/v1/billing/license")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.json()["claims"]["tier"], "enterprise")
        active_license = self.client.get("/api/v1/license")
        self.assertEqual(active_license.status_code, 200, active_license.text)
        self.assertEqual(active_license.json()["active_tier"], "enterprise")
        self.assertEqual(download.json()["delivery_status"], "PENDING")
        retry = self.client.post("/api/v1/billing/license/retry")
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["status"], "PENDING")

        bad = self.client.post(
            "/api/v1/billing/stripe/webhook",
            content=payload + b" ",
            headers=headers,
        )
        self.assertEqual(bad.status_code, 400)

    def test_fresh_community_instance(self) -> None:
        license_response = self.client.get("/api/v1/license")
        self.assertEqual(license_response.status_code, 200)
        license_info = license_response.json()
        self.assertEqual(license_info["active_tier"], "community")
        self.assertEqual(license_info["max_nodes"], 3)

        nodes_response = self.client.get("/api/v1/nodes")
        self.assertEqual(nodes_response.status_code, 200)
        self.assertEqual(nodes_response.json(), {"nodes": [], "total": 0})

    def test_builtin_rules_and_default_group_are_seeded(self) -> None:
        groups_response = self.client.get("/api/v1/groups")
        self.assertEqual(groups_response.status_code, 200)
        groups = groups_response.json()["groups"]
        self.assertTrue(any(group["slug"] == "default" for group in groups))

        rules_response = self.client.get("/api/v1/alert-rules")
        self.assertEqual(rules_response.status_code, 200)
        rules = rules_response.json()["rules"]
        self.assertGreater(len(rules), 0)
        self.assertTrue(all(rule["is_builtin"] for rule in rules))

    def test_default_branding_contains_no_enterprise_assets(self) -> None:
        response = self.client.get("/api/v1/branding")
        self.assertEqual(response.status_code, 200)
        branding = response.json()
        self.assertFalse(branding["white_label"])
        self.assertEqual(branding["product_name"], "Control Plane")
        self.assertIsNone(branding["logo_url"])


if __name__ == "__main__":
    unittest.main()
