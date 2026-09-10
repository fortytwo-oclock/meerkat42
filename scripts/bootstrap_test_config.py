#!/usr/bin/env python3
"""Generate isolated credentials, an enterprise test license, and Vault fixtures."""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from hub.billing.issuer import issue_license


def atomic_write(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def generate(root: Path) -> dict[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode()
    public_b64 = base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode()
    postgres_password = secrets.token_urlsafe(24)
    values = {
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": f"postgresql+asyncpg://control_plane:{postgres_password}@postgres:5432/control_plane",
        "ADMIN_TOKEN": "test_owner_" + secrets.token_urlsafe(24),
        "AGENT_BOOTSTRAP_TOKEN": "test_agent_" + secrets.token_urlsafe(24),
        "ED25519_PUBLIC_KEY": public_b64,
        "EMERGENCY_CONSOLE_ENABLED": "true",
        "EMERGENCY_TOTP_SECRET": base64.b32encode(secrets.token_bytes(20)).decode().rstrip("="),
        "REMOTE_COMMAND_TWO_PERSON_APPROVAL": "false",
        "VAULT_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
    }
    env_path = root / "deploy" / "test.env"
    atomic_write(env_path, "\n".join(f"{key}={value}" for key, value in values.items()) + "\n")

    license_token, _ = issue_license(
        private_b64,
        subject="",
        customer_name="Control Plane Test Lab",
        plan={
            "tier": "enterprise", "max_nodes": 100, "features": ["*"],
            "white_label_name": "Control Plane Test Lab",
            "white_label_color": "262 83% 58%",
        },
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        expected_public_key_b64=public_b64,
    )
    atomic_write(root / "deploy" / "test-license.txt", license_token + "\n")
    atomic_write(
        root / "deploy" / "test-totp-uri.txt",
        "otpauth://totp/ControlPlane:test-owner?issuer=ControlPlane&secret="
        + values["EMERGENCY_TOTP_SECRET"] + "&digits=6&period=30\n",
    )
    fixture = root / "deploy" / "test-fixtures" / "compose"
    atomic_write(fixture / "docker-compose.yml", "services:\n  fixture:\n    image: alpine:3.20\n", 0o644)
    atomic_write(fixture / ".env", "FIXTURE_SECRET=encrypted-before-transport\n", 0o600)
    return {"env": str(env_path), "license": str(root / "deploy" / "test-license.txt"),
            "totp_uri": str(root / "deploy" / "test-totp-uri.txt")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    created = generate(args.root.resolve())
    print("Generated disposable test configuration:")
    for name, value in created.items():
        print(f"  {name}: {value}")
    print("Treat every generated value as a secret even though this is a test environment.")


if __name__ == "__main__":
    main()
