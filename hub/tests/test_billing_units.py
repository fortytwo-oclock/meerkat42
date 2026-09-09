from __future__ import annotations

import base64
import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from hub.billing.issuer import LicenseIssuanceError, issue_license
from hub.billing.stripe import StripeWebhookError, verify_signature
from hub.license_manager import LicenseManager


class StripeSignatureTest(unittest.TestCase):
    def test_valid_signature_and_multiple_v1_values(self) -> None:
        payload = b'{"id":"evt_test"}'
        timestamp = 1_800_000_000
        digest = hmac.new(
            b"whsec_test",
            str(timestamp).encode() + b"." + payload,
            hashlib.sha256,
        ).hexdigest()
        verify_signature(
            payload,
            f"t={timestamp},v1=wrong,v1={digest}",
            "whsec_test",
            now=timestamp,
        )

    def test_rejects_tampered_and_stale_payloads(self) -> None:
        payload = b"original"
        timestamp = 1_800_000_000
        digest = hmac.new(
            b"whsec_test",
            str(timestamp).encode() + b"." + payload,
            hashlib.sha256,
        ).hexdigest()
        with self.assertRaises(StripeWebhookError):
            verify_signature(
                b"tampered",
                f"t={timestamp},v1={digest}",
                "whsec_test",
                now=timestamp,
            )
        with self.assertRaises(StripeWebhookError):
            verify_signature(
                payload,
                f"t={timestamp},v1={digest}",
                "whsec_test",
                tolerance_seconds=300,
                now=timestamp + 301,
            )


class LicenseIssuerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.private = ed25519.Ed25519PrivateKey.generate()
        self.private_b64 = base64.b64encode(
            self.private.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        ).decode()
        self.public_b64 = base64.b64encode(
            self.private.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode()

    def test_issued_token_passes_offline_verifier(self) -> None:
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        token, claims = issue_license(
            self.private_b64,
            subject="tenant-1",
            customer_name="Example MSP",
            plan={
                "tier": "enterprise",
                "max_nodes": 100,
                "features": ["vault_backup", "white_label"],
                "white_label_name": "Example Monitor",
            },
            issued_at=now,
            expires_at=now + timedelta(days=30),
            expected_public_key_b64=self.public_b64,
        )
        verified = LicenseManager(self.public_b64).verify(token)
        self.assertEqual(verified, claims)
        self.assertEqual(verified["tier"], "enterprise")
        self.assertEqual(verified["max_nodes"], 100)

    def test_rejects_mismatched_public_key(self) -> None:
        other_public = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        with self.assertRaises(LicenseIssuanceError):
            issue_license(
                self.private_b64,
                subject="tenant-1",
                customer_name="Example MSP",
                plan={"tier": "enterprise", "max_nodes": 10, "features": []},
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                expected_public_key_b64=base64.b64encode(other_public).decode(),
            )
