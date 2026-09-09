#!/usr/bin/env python3
"""Offline decryption for Infrastructure Vault backups.

The hub only ever stores ciphertext. Download a backup with:

    curl -H "X-Admin-Token: $ADMIN_TOKEN" \
         http://hub:8080/api/v1/backups/<backup_id> > backup.json

then restore it locally with the same key the agent used (VAULT_KEY):

    python scripts/vault_decrypt.py --key "$VAULT_KEY" --in backup.json --out ./restored

AES-256-GCM, 12-byte nonce, AAD = the file's relative path (must match on decrypt).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", required=True, help="base64 (standard) 32-byte AES-256 key")
    ap.add_argument("--in", dest="infile", required=True, help="backup JSON from the hub")
    ap.add_argument("--out", default="./restored", help="output directory")
    args = ap.parse_args()

    try:
        key = base64.b64decode(args.key.strip())
    except Exception as exc:  # noqa: BLE001
        return _fail(f"cannot base64-decode key: {exc}")
    if len(key) != 32:
        return _fail(f"key must be 32 bytes, got {len(key)}")

    with open(args.infile, "rb") as fh:
        doc = json.load(fh)
    files = doc.get("files") if isinstance(doc, dict) else doc
    if not files:
        return _fail("no files in backup (status not COMPLETE?)")

    expected_fp = doc.get("key_fingerprint") if isinstance(doc, dict) else None
    if expected_fp and expected_fp != key_fingerprint(key):
        print(
            f"WARNING: key fingerprint mismatch "
            f"(backup={expected_fp} key={key_fingerprint(key)})",
            file=sys.stderr,
        )

    aes = AESGCM(key)
    ok = 0
    for f in files:
        nonce = base64.b64decode(f["nonce"])
        ct = base64.b64decode(f["ciphertext"])
        try:
            plain = aes.decrypt(nonce, ct, f["path"].encode())
        except Exception as exc:  # noqa: BLE001
            return _fail(f"decryption failed for {f['path']}: {exc}")
        if hashlib.sha256(plain).hexdigest() != f["sha256"]:
            return _fail(f"integrity check failed for {f['path']}")
        dest = os.path.join(args.out, f["path"])
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(plain)
        print(f"restored {dest} ({len(plain)} bytes)")
        ok += 1

    print(f"\n{ok} file(s) restored to {args.out}/")
    return 0


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
