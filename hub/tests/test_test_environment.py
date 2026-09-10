from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.license_manager import LicenseManager
from scripts.bootstrap_test_config import generate


class TestEnvironmentBootstrapTest(unittest.TestCase):
    def test_generates_matching_license_and_required_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = generate(root)
            env = {}
            for line in (root / "deploy" / "test.env").read_text().splitlines():
                key, value = line.split("=", 1)
                env[key] = value
            token = (root / "deploy" / "test-license.txt").read_text().strip()
            claims = LicenseManager(env["ED25519_PUBLIC_KEY"]).verify(token)
            self.assertEqual(claims["tier"], "enterprise")
            self.assertIn("*", claims["features"])
            self.assertEqual(len(env["VAULT_KEY"]), 44)
            self.assertTrue((root / "deploy" / "test-fixtures" / "compose" / ".env").is_file())
            self.assertNotIn("owner_token", generated)
