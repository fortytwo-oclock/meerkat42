#!/usr/bin/env python3
"""Exercise a disposable Compose environment from enrollment through diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.emergency import code_at


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


class API:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body=None, *, token=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request_headers = {"Accept": "application/json"}
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        selected = self.token if token is None else token
        if selected:
            request_headers["X-Admin-Token"] = selected
        request_headers.update(headers or {})
        req = Request(self.base + path, data=data, method=method, headers=request_headers)
        with urlopen(req, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw and "json" in response.headers.get("Content-Type", "") else raw.decode()


def wait_for(label: str, check, timeout: int = 60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                print(f"PASS  {label}")
                return value
        except Exception as exc:
            last = exc
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {label}: {last or 'condition not met'}")


def expect_http(label: str, status: int, action) -> None:
    try:
        action()
    except HTTPError as exc:
        if exc.code == status:
            print(f"PASS  {label}")
            return
        raise
    raise RuntimeError(f"{label}: expected HTTP {status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--env-file", type=Path, default=Path("deploy/test.env"))
    parser.add_argument("--license-file", type=Path, default=Path("deploy/test-license.txt"))
    parser.add_argument("--node-id", default="test-agent-01")
    args = parser.parse_args()
    env = load_env(args.env_file)
    api = API(args.base_url, env["ADMIN_TOKEN"])

    health = api.call("GET", "/api/v1/ready", token="")
    assert health["status"] == "healthy" and health["checks"]["database"] == "ok"
    print("PASS  database and realtime readiness")
    expect_http("unauthenticated tenant API is closed", 401, lambda: api.call("GET", "/api/v1/nodes", token=""))

    license_token = args.license_file.read_text(encoding="utf-8").strip()
    applied = api.call("POST", "/api/v1/license", {"license_token": license_token})
    assert applied["claims"]["tier"] == "enterprise"
    branding = api.call("GET", "/api/v1/branding", token="")
    assert branding["product_name"] == "Control Plane Test Lab"
    print("PASS  offline enterprise license and live branding")

    def online_node():
        nodes = api.call("GET", "/api/v1/nodes")["nodes"]
        return next((node for node in nodes if node["node_id"] == args.node_id and node["status"] == "ONLINE"), None)
    wait_for("agent enrollment and telemetry", online_node, 90)

    ping = api.call("POST", f"/api/v1/nodes/{args.node_id}/commands", {"action": "ping", "payload": {}},
                    headers={"Idempotency-Key": "smoke-ping"})
    def ping_done():
        command = api.call("GET", f"/api/v1/commands/{ping['id']}")
        if command["status"] in {"FAILED", "TIMED_OUT"}:
            raise RuntimeError(command.get("error"))
        return command if command["status"] == "SUCCEEDED" else None
    wait_for("durable command dispatch and result", ping_done, 45)

    backup = api.call("POST", f"/api/v1/nodes/{args.node_id}/backup", {})
    def backup_done():
        rows = api.call("GET", f"/api/v1/nodes/{args.node_id}/backups")["backups"]
        row = next(item for item in rows if item["id"] == backup["backup_id"])
        if row["status"] == "ERROR":
            raise RuntimeError(row.get("error"))
        return row if row["status"] == "COMPLETE" and row["file_count"] >= 2 else None
    wait_for("Vault encryption and ciphertext ingestion", backup_done, 45)

    counter = int(time.time()) // 30
    totp = code_at(env["EMERGENCY_TOTP_SECRET"], counter)
    emergency = api.call("POST", "/api/v1/emergency/sessions", {}, headers={"X-TOTP-Code": totp})
    diagnostic = api.call(
        "POST", f"/api/v1/nodes/{args.node_id}/diagnostics", {"diagnostic": "uptime"},
        headers={"X-Emergency-Grant": emergency["grant"]},
    )
    def diagnostic_done():
        command = api.call("GET", f"/api/v1/commands/{diagnostic['id']}")
        if command["status"] in {"FAILED", "TIMED_OUT"}:
            raise RuntimeError(command.get("error"))
        return command if command["status"] == "SUCCEEDED" and command.get("result", {}).get("output") else None
    wait_for("TOTP-gated bounded diagnostic", diagnostic_done, 45)
    api.call("DELETE", "/api/v1/emergency/sessions", headers={"X-Emergency-Grant": emergency["grant"]})

    viewer = api.call("POST", "/api/v1/tokens", {"name": "smoke viewer", "role": "viewer", "group_slug": "default"})
    visible = api.call("GET", "/api/v1/nodes", token=viewer["token"])["nodes"]
    assert any(node["node_id"] == args.node_id for node in visible)
    expect_http(
        "viewer mutation is forbidden", 403,
        lambda: api.call("POST", f"/api/v1/nodes/{args.node_id}/commands", {"action": "ping"}, token=viewer["token"]),
    )
    api.call("DELETE", f"/api/v1/tokens/{viewer['id']}")

    audit = api.call("GET", "/api/v1/audit-events?limit=100")["events"]
    assert any(event["action"] == "command.completed" for event in audit)
    metrics = api.call("GET", "/api/v1/ops/metrics")
    assert "control_plane_nodes" in metrics
    print("PASS  scoped RBAC, audit trail, and operational metrics")
    print("\nDisposable environment smoke test completed successfully.")


if __name__ == "__main__":
    main()
