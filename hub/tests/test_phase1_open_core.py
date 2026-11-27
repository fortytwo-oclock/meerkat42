from __future__ import annotations

import base64
import json
import subprocess
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from hub.billing.issuer import issue_license
from hub.config import get_settings
from hub.main import app
from hub.timeseries.base import MAX_DYNAMIC_METRICS_PER_FRAME, extract_scalars


class DynamicMetricsExtractionTest(unittest.TestCase):
    def test_extracts_structured_and_dynamic_metrics_with_sanitization(self) -> None:
        payload = {
            "host_metrics": {"cpu_usage_pct": 22.5, "ram_usage_pct": 51.0, "used_ram_mb": 4096},
            "battery": {"pct": 87.5, "charging": True, "temperature_c": 31.2},
            "network": {"rx_bps": 125000.0, "tx_bps": 42000.0},
            "disk_io": {"read_bps": 1048576.0, "write_bps": 524288.0},
            "metrics": {
                "net.rx_bps:eth0": 120000.0,
                "disk.read_iops:nvme0n1": 450,
                "thermal.cpu_c": 58.4,
                "invalid metric with spaces": 1.0,
                "nan_value": float("nan"),
                "inf_value": float("inf"),
                "non_numeric": "hello",
            },
        }
        scalars = extract_scalars(payload)
        self.assertEqual(scalars["cpu_usage_pct"], 22.5)
        self.assertEqual(scalars["battery.pct"], 87.5)
        self.assertEqual(scalars["battery.charging"], 1.0)
        self.assertEqual(scalars["battery.temp_c"], 31.2)
        self.assertEqual(scalars["net.rx_bps"], 125000.0)
        self.assertEqual(scalars["disk.read_bps"], 1048576.0)
        self.assertEqual(scalars["net.rx_bps:eth0"], 120000.0)
        self.assertEqual(scalars["disk.read_iops:nvme0n1"], 450.0)
        self.assertEqual(scalars["thermal.cpu_c"], 58.4)
        self.assertNotIn("invalid metric with spaces", scalars)
        self.assertNotIn("nan_value", scalars)
        self.assertNotIn("inf_value", scalars)
        self.assertNotIn("non_numeric", scalars)

    def test_caps_dynamic_metric_cardinality_per_frame(self) -> None:
        many = {f"custom.metric.{i}": float(i) for i in range(MAX_DYNAMIC_METRICS_PER_FRAME + 50)}
        scalars = extract_scalars({"metrics": many})
        custom_keys = [k for k in scalars if k.startswith("custom.metric.")]
        self.assertEqual(len(custom_keys), MAX_DYNAMIC_METRICS_PER_FRAME)


class Phase1IntegrationTest(unittest.TestCase):
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
        )
        settings.admin_token = "phase1-admin-token"
        settings.agent_bootstrap_token = "phase1-agent-token"
        settings.ed25519_public_key = cls._public_b64
        settings.background_workers_enabled = False

        cls.headers = {"Authorization": "Bearer phase1-admin-token"}
        cls.client = TestClient(app, headers=cls.headers)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        # Exit TestClient first so its event loop releases all SQLite handles
        # before cleanup runs on a fresh asyncio loop.
        tenant_id = app.state.default_tenant_id
        cls.client.__exit__(None, None, None)

        import asyncio
        from hub.db.session import engine, session_scope
        from hub.license_manager import COMMUNITY_CLAIMS
        from hub.repositories import licenses as lic_repo
        from hub.repositories import nodes as nodes_repo

        async def _cleanup() -> None:
            await engine.dispose()
            async with session_scope() as session:
                node = await nodes_repo.get(session, "mbp-alice")
                if node is not None:
                    await nodes_repo.delete(session, node)
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
        ) = cls._saved

    def _wait_for_node(self, node_id: str, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/v1/nodes/{node_id}")
            if resp.status_code == 200 and resp.json().get("last_location") is not None:
                return resp.json()
            time.sleep(0.05)
        self.fail(f"Timed out waiting for telemetry on {node_id}")

    def test_open_core_feature_catalog_and_enterprise_unlock(self) -> None:
        res = self.client.get("/api/v1/license/features")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["active_tier"], "community")
        self.assertIn("live_stream", body["community_features"])
        by_key = {item["key"]: item for item in body["enterprise_features"]}
        self.assertIn("sso_oidc", by_key)
        self.assertIn("mdm_fleet", by_key)
        self.assertIn("geofencing", by_key)
        self.assertFalse(by_key["sso_oidc"]["enabled"])

        token, _ = issue_license(
            private_key_b64=self._private_b64,
            subject="",
            customer_name="Acme Corp",
            plan={
                "tier": "enterprise",
                "max_nodes": -1,
                "features": ["sso_oidc", "mdm_fleet", "geofencing"],
            },
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        applied = self.client.post("/api/v1/license", json={"license_token": token})
        self.assertEqual(applied.status_code, 200, applied.text)

        res2 = self.client.get("/api/v1/license/features")
        by_key2 = {item["key"]: item for item in res2.json()["enterprise_features"]}
        self.assertTrue(by_key2["sso_oidc"]["enabled"])
        self.assertTrue(by_key2["mdm_fleet"]["enabled"])
        self.assertTrue(by_key2["geofencing"]["enabled"])
        self.assertFalse(by_key2["white_label"]["enabled"])

    def test_multi_device_ingest_filtering_metric_catalog_and_stream_rate(self) -> None:
        with self.client.websocket_connect(
            "/api/v1/agent/stream",
            headers={"X-Auth-Token": "phase1-agent-token"},
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "node_id": "mbp-alice",
                        "hostname": "alice-macbook-pro",
                        "platform": "macos",
                        "device_class": "workstation",
                        "arch": "arm64",
                        "agent_version": "2.0.0",
                        "hardware": {"model": "MacBookPro18,1", "cpu": "Apple M1 Pro"},
                    }
                )
            )
            ws.send_text(
                json.dumps(
                    {
                        "node_id": "mbp-alice",
                        "hostname": "alice-macbook-pro",
                        "os": "Darwin 24.0",
                        "platform": "macos",
                        "device_class": "workstation",
                        "arch": "arm64",
                        "uptime_sec": 7200,
                        "host_metrics": {"cpu_usage_pct": 18.0, "ram_usage_pct": 62.0, "load_1": 1.4},
                        "battery": {"pct": 91.0, "charging": False},
                        "location": {
                            "lat": 37.7749,
                            "lon": -122.4194,
                            "accuracy_m": 15.0,
                            "zone": "SF HQ",
                            "source": "corelocation",
                        },
                        "metrics": {
                            "net.rx_bps:en0": 512000.0,
                            "gpu.util_pct": 12.5,
                        },
                    }
                )
            )

            detail = self._wait_for_node("mbp-alice")
            self.assertEqual(detail["hardware_info"]["model"], "MacBookPro18,1")
            self.assertAlmostEqual(detail["last_location"]["lat"], 37.7749)

            filtered = self.client.get("/api/v1/nodes?device_class=workstation&platform=macos")
            self.assertEqual(filtered.status_code, 200)
            nodes = filtered.json()["nodes"]
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0]["node_id"], "mbp-alice")
            self.assertEqual(nodes[0]["device_class"], "workstation")
            self.assertEqual(nodes[0]["platform"], "macos")
            self.assertEqual(nodes[0]["battery_pct"], 91.0)
            self.assertEqual(nodes[0]["last_location"]["zone"], "SF HQ")

            mobile_only = self.client.get("/api/v1/nodes?device_class=mobile")
            self.assertEqual(mobile_only.json()["total"], 0)

            catalog = self.client.get("/api/v1/nodes/mbp-alice/metrics/catalog")
            self.assertEqual(catalog.status_code, 200)
            discovered = catalog.json()["metrics"]
            self.assertIn("battery.pct", discovered)
            self.assertIn("net.rx_bps:en0", discovered)
            self.assertIn("gpu.util_pct", discovered)

            series = self.client.get("/api/v1/nodes/mbp-alice/metrics?metric=net.rx_bps:en0&range_hours=1")
            self.assertEqual(series.status_code, 200)
            self.assertEqual(len(series.json()["points"]), 1)
            self.assertEqual(series.json()["points"][0]["value"], 512000.0)

            rate_resp = self.client.post(
                "/api/v1/nodes/mbp-alice/stream-rate",
                json={"interval_sec": 1, "ttl_sec": 45},
            )
            self.assertEqual(rate_resp.status_code, 200, rate_resp.text)
            cmd_frame = json.loads(ws.receive_text())
            self.assertEqual(cmd_frame["action"], "set_stream_rate")
            self.assertEqual(cmd_frame["payload"]["interval_sec"], 1)
            self.assertEqual(cmd_frame["payload"]["ttl_sec"], 45)

        offline_rate = self.client.post(
            "/api/v1/nodes/mbp-alice/stream-rate",
            json={"interval_sec": 1, "ttl_sec": 30},
        )
        self.assertEqual(offline_rate.status_code, 409)


class NativeInstallerScriptTest(unittest.TestCase):
    def test_install_hub_script_syntax_and_dry_run(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        script = repo_root / "scripts" / "install-hub.sh"
        self.assertTrue(script.is_file())

        syntax = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        dry = subprocess.run(
            ["bash", str(script), "--mode", "sqlite", "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("[dry-run]", dry.stdout)
        self.assertIn("meerkat42-hub.service", dry.stdout)


if __name__ == "__main__":
    unittest.main()
