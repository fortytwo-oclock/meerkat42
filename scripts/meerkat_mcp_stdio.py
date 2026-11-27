#!/usr/bin/env python3
"""Zero-dependency stdio-to-Streamable-HTTP MCP bridge for OpenMeerkat Hub.

Usage:
    MEERKAT_HUB_URL=https://app.openmeerkat.com \
    MEERKAT_API_TOKEN=mk_... \
    python3 scripts/meerkat_mcp_stdio.py

Reads newline-delimited JSON-RPC 2.0 MCP requests from stdin, forwards them
to `<MEERKAT_HUB_URL>/api/v1/mcp`, and writes responses back to stdout.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    hub_url = os.environ.get("MEERKAT_HUB_URL", "http://localhost:8080").rstrip("/")
    token = os.environ.get("MEERKAT_API_TOKEN", "").strip()
    endpoint = f"{hub_url}/api/v1/mcp"

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue

        req_id = payload.get("id") if isinstance(payload, dict) else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8").strip()
                if body:
                    sys.stdout.write(body + "\n")
                    sys.stdout.flush()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if req_id is not None:
                err = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Hub HTTP {exc.code}: {detail}"},
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            if req_id is not None:
                err = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Bridge error: {exc}"},
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
