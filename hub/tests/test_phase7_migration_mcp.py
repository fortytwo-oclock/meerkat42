"""Tests for Phase 7: Cloud <-> Self-Hosted migration, custom subdomains, Fleet API, and MCP server."""
from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from hub.config import Settings, get_settings
from hub.main import app
from hub.remote.policy import CommandPolicyError, validate_command
from hub.tenant_routing import build_routing_info, extract_subdomain_slug, validate_tenant_slug


class SubdomainAndPolicyUnitTest(unittest.TestCase):
    def test_extract_subdomain_slug_handles_valid_and_reserved_hosts(self) -> None:
        settings = Settings(cloud_base_domain="openmeerkat.com")
        self.assertEqual(extract_subdomain_slug("acme.openmeerkat.com", settings), "acme")
        self.assertEqual(extract_subdomain_slug("acme-ops.openmeerkat.com:443", settings), "acme-ops")
        # Reserved subdomains and multi-level subdomains are ignored
        self.assertIsNone(extract_subdomain_slug("app.openmeerkat.com", settings))
        self.assertIsNone(extract_subdomain_slug("api.openmeerkat.com", settings))
        self.assertIsNone(extract_subdomain_slug("www.openmeerkat.com", settings))
        self.assertIsNone(extract_subdomain_slug("a.b.openmeerkat.com", settings))
        self.assertIsNone(extract_subdomain_slug("openmeerkat.com", settings))
        self.assertIsNone(extract_subdomain_slug("other.example.com", settings))

    def test_validate_tenant_slug_rejects_reserved_and_invalid(self) -> None:
        settings = Settings(cloud_base_domain="openmeerkat.com")
        self.assertEqual(validate_tenant_slug("Button-House", settings), "button-house")
        for bad in ("ab", "app", "api", "www", "bad_slug", "-leading", "trailing-"):
            with self.assertRaises(ValueError):
                validate_tenant_slug(bad, settings)

    def test_routing_info_cloud_vs_self_hosted(self) -> None:
        cloud_settings = Settings(cloud_base_domain="openmeerkat.com")
        info = build_routing_info("acme", "Acme Corp", cloud_settings)
        self.assertEqual(info["mode"], "cloud")
        self.assertEqual(info["vanity_subdomain_url"], "https://acme.openmeerkat.com")
        self.assertEqual(info["mcp_endpoint"], "https://acme.openmeerkat.com/api/v1/mcp")

        self_settings = Settings(cloud_base_domain="")
        local_info = build_routing_info(
            "default", "Default", self_settings, request_base_url="https://hub.internal.local/"
        )
        self.assertEqual(local_info["mode"], "self_hosted")
        self.assertIsNone(local_info["vanity_subdomain_url"])
        self.assertEqual(local_info["mcp_endpoint"], "https://hub.internal.local/api/v1/mcp")

    def test_migrate_hub_command_policy_validation(self) -> None:
        policy, payload = validate_command(
            "migrate_hub",
            {"hub_url": "https://hub.myhome.net/", "enrollment_token": "tok_123", "verify_tls": True},
        )
        self.assertTrue(policy.destructive)
        self.assertEqual(payload["hub_url"], "https://hub.myhome.net")
        self.assertEqual(payload["enrollment_token"], "tok_123")

        # Reject plain HTTP for remote hosts
        with self.assertRaises(CommandPolicyError):
            validate_command("migrate_hub", {"hub_url": "http://remote.example.com"})

        # Allow http://localhost for dev
        _, local_payload = validate_command("migrate_hub", {"hub_url": "http://localhost:8080"})
        self.assertEqual(local_payload["hub_url"], "http://localhost:8080")


class MigrationAndMcpApiTest(unittest.TestCase):
    def _mcp(
        self,
        client: TestClient,
        method: str,
        params: dict | None = None,
        *,
        headers: dict,
        req_id: int = 1,
    ):
        body: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            body["params"] = params
        return client.post("/api/v1/mcp", json=body, headers=headers)

    def test_end_to_end_export_import_routing_and_agent_migration(self) -> None:
        settings = get_settings()
        saved = (
            settings.admin_token,
            settings.cloud_base_domain,
            settings.background_workers_enabled,
        )
        settings.admin_token = "owner-secret-phase7"
        settings.cloud_base_domain = "openmeerkat.com"
        settings.background_workers_enabled = False
        owner_headers = {"Authorization": "Bearer owner-secret-phase7"}

        created_channel_id = None
        created_rule_id = None
        orig_slug = None
        orig_name = None
        try:
            with TestClient(app) as client:
                initial_routing = client.get("/api/v1/tenant/routing", headers=owner_headers)
                self.assertEqual(initial_routing.status_code, 200, initial_routing.text)
                orig_slug = initial_routing.json()["tenant_slug"]
                orig_name = initial_routing.json()["tenant_name"]

                # 1. Create a custom alert rule and notification channel
                r = client.post(
                    "/api/v1/alert-rules",
                    json={
                        "name": "High CPU Lookout P7",
                        "metric": "cpu_usage_pct",
                        "comparator": "gt",
                        "threshold": 88.0,
                        "duration_sec": 30,
                        "severity": "HIGH",
                    },
                    headers=owner_headers,
                )
                self.assertIn(r.status_code, (200, 201), r.text)
                created_rule_id = r.json().get("id")

                ch = client.post(
                    "/api/v1/channels",
                    json={
                        "name": "Ops Mail P7",
                        "kind": "smtp",
                        "config": {
                            "host": "smtp.example.com",
                            "port": 587,
                            "username": "ops",
                            "password": "secret-smtp-password",
                            "from": "alerts@example.com",
                            "to": ["oncall@example.com"],
                        },
                        "min_severity": "WARNING",
                    },
                    headers=owner_headers,
                )
                self.assertIn(ch.status_code, (200, 201), ch.text)
                created_channel_id = ch.json().get("id")

                # 2. Customize tenant routing slug -> acme-fleet.openmeerkat.com
                routing = client.patch(
                    "/api/v1/tenant/routing",
                    json={"slug": "acme-fleet", "name": "Acme Fleet"},
                    headers=owner_headers,
                )
                self.assertEqual(routing.status_code, 200, routing.text)
                rdata = routing.json()
                self.assertEqual(rdata["tenant_slug"], "acme-fleet")
                self.assertEqual(rdata["vanity_subdomain_url"], "https://acme-fleet.openmeerkat.com")

                # Reserved slug should fail with 400
                bad_slug = client.patch(
                    "/api/v1/tenant/routing",
                    json={"slug": "api"},
                    headers=owner_headers,
                )
                self.assertEqual(bad_slug.status_code, 400)

                # 3. Export workspace (redacted by default)
                exp = client.get("/api/v1/tenant/export", headers=owner_headers)
                self.assertEqual(exp.status_code, 200, exp.text)
                bundle = exp.json()
                self.assertEqual(bundle["format"], "openmeerkat_export_v1")
                self.assertTrue(any(ar["name"] == "High CPU Lookout P7" for ar in bundle["alert_rules"]))
                ops_ch = [c for c in bundle["notification_channels"] if c["name"] == "Ops Mail P7"]
                self.assertEqual(len(ops_ch), 1)
                self.assertTrue(ops_ch[0]["secrets_redacted"])
                self.assertEqual(ops_ch[0]["config"].get("password"), "***")

                # 4. Import a portable bundle containing a new group + alert rule + node
                portable = {
                    "format": "openmeerkat_export_v1",
                    "groups": [{"slug": "homelab-p7", "name": "Homelab P7", "description": "Home racks"}],
                    "alert_rules": [
                        {
                            "name": "Homelab RAM Alert P7",
                            "group_slug": "homelab-p7",
                            "metric": "ram_usage_pct",
                            "comparator": "gt",
                            "threshold": 90.0,
                            "duration_sec": 60,
                            "severity": "WARNING",
                            "enabled": True,
                        }
                    ],
                    "notification_channels": [],
                    "nodes": [
                        {
                            "node_id": "node-linux-p7-01",
                            "hostname": "prod-api-01",
                            "group_slug": "homelab-p7",
                            "os": "Ubuntu 24.04",
                            "platform": "linux",
                            "device_class": "server",
                            "arch": "amd64",
                            "labels": {"env": "prod"},
                            "last_telemetry": {"cpu_usage_pct": 42.5, "ram_usage_pct": 63.0},
                        }
                    ],
                }
                imp = client.post(
                    "/api/v1/tenant/import",
                    json={"bundle": portable, "import_nodes": True},
                    headers=owner_headers,
                )
                self.assertEqual(imp.status_code, 200, imp.text)
                counts = imp.json()["counts"]
                self.assertEqual(counts["groups_created"], 1)
                self.assertEqual(counts["alert_rules_created"], 1)
                self.assertEqual(counts["nodes_imported"], 1)

                # Re-importing the exact same bundle is idempotent
                imp2 = client.post(
                    "/api/v1/tenant/import",
                    json={"bundle": portable, "import_nodes": True},
                    headers=owner_headers,
                )
                self.assertEqual(imp2.status_code, 200)
                self.assertEqual(imp2.json()["counts"]["groups_created"], 0)
                self.assertEqual(imp2.json()["counts"]["alert_rules_created"], 0)
                self.assertEqual(imp2.json()["counts"]["nodes_skipped"], 1)

                # 5. Fleet summary reflects imported node & telemetry
                summary = client.get("/api/v1/fleet/summary", headers=owner_headers)
                self.assertEqual(summary.status_code, 200, summary.text)
                sdata = summary.json()
                self.assertGreaterEqual(sdata["total_nodes"], 1)
                self.assertGreaterEqual(sdata["by_platform"].get("linux", 0), 1)
                self.assertTrue(any(n["node_id"] == "node-linux-p7-01" for n in sdata["top_cpu_nodes"]))

                # 6. Queue agent migration to self-hosted Hub
                mig = client.post(
                    "/api/v1/tenant/migrate-agents",
                    json={
                        "hub_url": "https://meerkat.selfhosted.example.com",
                        "enrollment_token": "self_tok_999",
                        "node_ids": ["node-linux-p7-01"],
                    },
                    headers=owner_headers,
                )
                self.assertEqual(mig.status_code, 202, mig.text)
                mdata = mig.json()
                self.assertEqual(mdata["queued_count"], 1)
                self.assertEqual(mdata["commands"][0]["node_id"], "node-linux-p7-01")

                # Cleanup synthetic test records (including imported non-builtin alert rule)
                client.delete("/api/v1/nodes/node-linux-p7-01", headers=owner_headers)
                if created_channel_id:
                    client.delete(f"/api/v1/channels/{created_channel_id}", headers=owner_headers)
                all_rules = client.get("/api/v1/alert-rules", headers=owner_headers).json().get("rules", [])
                for rule in all_rules:
                    if not rule.get("is_builtin") and rule.get("name", "").endswith("P7"):
                        client.delete(f"/api/v1/alert-rules/{rule['id']}", headers=owner_headers)
                if orig_slug:
                    # Restore slug back via direct DB update if it was 'default' (reserved)
                    import asyncio
                    from sqlalchemy import delete as sa_delete, update
                    from hub.db.session import session_scope
                    from hub.models import ClientGroup, Tenant

                    async def _restore_state():
                        async with session_scope() as s:
                            await s.execute(
                                sa_delete(ClientGroup).where(ClientGroup.slug == "homelab-p7")
                            )
                            await s.execute(
                                update(Tenant)
                                .where(Tenant.slug == "acme-fleet")
                                .values(slug=orig_slug, name=orig_name or "Default")
                            )

                    asyncio.run(_restore_state())
        finally:
            settings.admin_token, settings.cloud_base_domain, settings.background_workers_enabled = saved

    def test_mcp_protocol_tools_resources_and_rbac(self) -> None:
        settings = get_settings()
        saved = (
            settings.admin_token,
            settings.cloud_base_domain,
            settings.background_workers_enabled,
        )
        settings.admin_token = "owner-secret-mcp"
        settings.cloud_base_domain = "openmeerkat.com"
        settings.background_workers_enabled = False
        owner_headers = {"Authorization": "Bearer owner-secret-mcp"}

        token_id = None
        created_rule_id = None
        try:
            with TestClient(app) as client:
                client.post(
                    "/api/v1/tenant/import",
                    json={
                        "bundle": {
                            "format": "openmeerkat_export_v1",
                            "groups": [{"slug": "workstations-p7", "name": "Workstations P7"}],
                            "alert_rules": [],
                            "notification_channels": [],
                            "nodes": [
                                {
                                    "node_id": "mcp-node-p7-01",
                                    "hostname": "studio-mac",
                                    "group_slug": "workstations-p7",
                                    "os": "macOS 15",
                                    "platform": "macos",
                                    "device_class": "workstation",
                                    "last_telemetry": {"cpu_usage_pct": 18.0, "ram_usage_pct": 54.0},
                                }
                            ],
                        },
                        "import_nodes": True,
                    },
                    headers=owner_headers,
                )

                # 1. Discovery manifest
                manifest = client.get("/api/v1/mcp/manifest")
                self.assertEqual(manifest.status_code, 200)
                self.assertTrue(manifest.json()["enabled"])
                self.assertIn("get_fleet_summary", manifest.json()["tools"])

                # 2. Initialize + tools/list + resources/list
                init = self._mcp(client, "initialize", headers=owner_headers)
                self.assertEqual(init.status_code, 200)
                self.assertEqual(init.json()["result"]["serverInfo"]["name"], "openmeerkat-hub")

                tools = self._mcp(client, "tools/list", headers=owner_headers)
                self.assertEqual(tools.status_code, 200)
                tool_names = {t["name"] for t in tools.json()["result"]["tools"]}
                self.assertIn("get_fleet_summary", tool_names)
                self.assertIn("run_node_command", tool_names)
                self.assertIn("export_workspace", tool_names)

                res_list = self._mcp(client, "resources/list", headers=owner_headers)
                self.assertEqual(res_list.status_code, 200)
                uris = {r["uri"] for r in res_list.json()["result"]["resources"]}
                self.assertIn("openmeerkat://fleet/summary", uris)

                # 3. Call read tools & read resource
                fleet_call = self._mcp(
                    client, "tools/call", {"name": "get_fleet_summary", "arguments": {}}, headers=owner_headers
                )
                self.assertFalse(fleet_call.json()["result"]["isError"])
                fleet_payload = json.loads(fleet_call.json()["result"]["content"][0]["text"])
                self.assertGreaterEqual(fleet_payload["total_nodes"], 1)

                node_call = self._mcp(
                    client,
                    "tools/call",
                    {"name": "get_node_details", "arguments": {"node_id": "mcp-node-p7-01"}},
                    headers=owner_headers,
                )
                self.assertFalse(node_call.json()["result"]["isError"])
                node_payload = json.loads(node_call.json()["result"]["content"][0]["text"])
                self.assertEqual(node_payload["hostname"], "studio-mac")

                res_read = self._mcp(
                    client, "resources/read", {"uri": "openmeerkat://nodes"}, headers=owner_headers
                )
                self.assertEqual(res_read.status_code, 200)
                self.assertIn("mcp-node-p7-01", res_read.json()["result"]["contents"][0]["text"])

                # 4. Call mutating MCP tools as owner
                create_alert = self._mcp(
                    client,
                    "tools/call",
                    {
                        "name": "create_alert_rule",
                        "arguments": {
                            "name": "MCP CPU Watch P7",
                            "metric": "cpu_usage_pct",
                            "comparator": "gt",
                            "threshold": 92,
                            "severity": "CRITICAL",
                        },
                    },
                    headers=owner_headers,
                )
                self.assertFalse(create_alert.json()["result"]["isError"])
                created_rule_id = json.loads(create_alert.json()["result"]["content"][0]["text"])["id"]

                cmd_call = self._mcp(
                    client,
                    "tools/call",
                    {
                        "name": "run_node_command",
                        "arguments": {"node_id": "mcp-node-p7-01", "action": "ping", "payload": {}},
                    },
                    headers=owner_headers,
                )
                self.assertFalse(cmd_call.json()["result"]["isError"])
                cmd_id = json.loads(cmd_call.json()["result"]["content"][0]["text"])["command_id"]

                cmd_status = self._mcp(
                    client,
                    "tools/call",
                    {"name": "get_command_status", "arguments": {"command_id": cmd_id}},
                    headers=owner_headers,
                )
                self.assertFalse(cmd_status.json()["result"]["isError"])

                # 5. VIEWER API token: allowed read tools, blocked mutating tools
                tok_res = client.post(
                    "/api/v1/tokens",
                    json={"name": "ai-readonly-p7", "role": "viewer"},
                    headers=owner_headers,
                )
                self.assertIn(tok_res.status_code, (200, 201), tok_res.text)
                token_id = tok_res.json().get("id")
                viewer_headers = {"Authorization": f"Bearer {tok_res.json()['token']}"}

                viewer_read = self._mcp(
                    client,
                    "tools/call",
                    {"name": "list_nodes", "arguments": {"platform": "macos"}},
                    headers=viewer_headers,
                )
                self.assertFalse(viewer_read.json()["result"]["isError"])

                viewer_cmd = self._mcp(
                    client,
                    "tools/call",
                    {"name": "run_node_command", "arguments": {"node_id": "mcp-node-p7-01", "action": "ping"}},
                    headers=viewer_headers,
                )
                self.assertTrue(viewer_cmd.json()["result"]["isError"])
                self.assertIn("Forbidden", viewer_cmd.json()["result"]["content"][0]["text"])

                viewer_export = self._mcp(
                    client,
                    "tools/call",
                    {"name": "export_workspace", "arguments": {}},
                    headers=viewer_headers,
                )
                self.assertTrue(viewer_export.json()["result"]["isError"])

                # Cleanup
                client.delete("/api/v1/nodes/mcp-node-p7-01", headers=owner_headers)
                if created_rule_id:
                    client.delete(f"/api/v1/alert-rules/{created_rule_id}", headers=owner_headers)
                if token_id:
                    client.delete(f"/api/v1/tokens/{token_id}", headers=owner_headers)
        finally:
            settings.admin_token, settings.cloud_base_domain, settings.background_workers_enabled = saved


if __name__ == "__main__":
    unittest.main()
