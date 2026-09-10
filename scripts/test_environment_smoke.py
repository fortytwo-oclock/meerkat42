#!/usr/bin/env python3
"""Exercise a disposable Compose environment from enrollment through diagnostics."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
    parser.add_argument(
        "--skip-emergency",
        action="store_true",
        help="Skip the TOTP diagnostic when break-glass access is disabled",
    )
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

    # Exercise a persisted scalar rule through a later telemetry transaction.
    # This catches enum/string deserialization mistakes that fresh in-memory
    # rule objects do not expose.
    for existing in api.call("GET", "/api/v1/alert-rules")["rules"]:
        if existing["name"] == "smoke persisted CPU rule" and not existing["is_builtin"]:
            api.call("DELETE", f"/api/v1/alert-rules/{existing['id']}")
    threshold_rule = api.call(
        "POST",
        "/api/v1/alert-rules",
        {
            "name": "smoke persisted CPU rule",
            "metric": "cpu_usage_pct",
            "comparator": "gt",
            "threshold": -1,
            "duration_sec": 2,
            "severity": "INFO",
        },
    )

    def threshold_firing():
        rows = api.call("GET", "/api/v1/alerts")["alerts"]
        return next(
            (
                row
                for row in rows
                if row["node_id"] == args.node_id
                and row["type"] == "THRESHOLD_CPU_USAGE_PCT"
            ),
            None,
        )

    assert threshold_firing() is None
    print("PASS  sustained alert does not fire immediately")
    wait_for("persisted sustained alert rule evaluation", threshold_firing, 30)
    api.call(
        "PATCH",
        f"/api/v1/alert-rules/{threshold_rule['id']}",
        {"threshold": 101},
    )

    def threshold_resolved():
        return not threshold_firing()

    wait_for("custom alert resolution", threshold_resolved, 30)
    api.call("DELETE", f"/api/v1/alert-rules/{threshold_rule['id']}")

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
    completed_backup = wait_for("Vault encryption and ciphertext ingestion", backup_done, 45)
    backup_document = api.call("GET", f"/api/v1/backups/{completed_backup['id']}")
    serialized_backup = json.dumps(backup_document)
    assert "FIXTURE_SECRET" not in serialized_backup
    key = base64.b64decode(env["VAULT_KEY"])
    cipher = AESGCM(key)
    restored: dict[str, bytes] = {}
    for encrypted in backup_document["files"]:
        plaintext = cipher.decrypt(
            base64.b64decode(encrypted["nonce"]),
            base64.b64decode(encrypted["ciphertext"]),
            encrypted["path"].encode(),
        )
        assert hashlib.sha256(plaintext).hexdigest() == encrypted["sha256"]
        restored[encrypted["path"]] = plaintext
    fixture_root = ROOT / "deploy" / "test-fixtures" / "compose"
    assert restored["docker-compose.yml"] == (fixture_root / "docker-compose.yml").read_bytes()
    assert restored[".env"] == (fixture_root / ".env").read_bytes()
    print("PASS  Vault ciphertext-only storage and offline round-trip decryption")

    if args.skip_emergency:
        print("SKIP  TOTP-gated diagnostic (break-glass access disabled)")
    else:
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
