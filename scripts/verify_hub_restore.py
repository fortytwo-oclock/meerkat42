#!/usr/bin/env python3
"""Verify a Hub backup by restoring it into an isolated drill target."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from hub_backup import normalized_postgres_url

REQUIRED_TABLES = {"tenants", "client_groups", "nodes", "license_records", "alembic_version"}


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_manifest(backup: Path) -> dict:
    manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("filename") != backup.name or manifest.get("sha256") != sha256(backup):
        raise RuntimeError("backup manifest or checksum does not match")
    return manifest


def verify_sqlite(backup: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="hub-restore-drill-") as directory:
        target = Path(directory) / "restored.sqlite3"
        with closing(sqlite3.connect(backup)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
        with closing(sqlite3.connect(target)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if integrity != "ok" or not REQUIRED_TABLES.issubset(tables):
                raise RuntimeError("restored SQLite database failed structural verification")
            tenant_count = db.execute("SELECT count(*) FROM tenants").fetchone()[0]
    return {"integrity": integrity, "tenant_count": tenant_count}


def verify_postgres(backup: Path, restore_url: str | None) -> dict:
    if not restore_url:
        raise RuntimeError("--restore-url is required for a PostgreSQL restore drill")
    parsed = urlsplit(restore_url)
    database = parsed.path.lstrip("/")
    if not database.endswith("_restore_drill"):
        raise RuntimeError("PostgreSQL drill database name must end with _restore_drill")
    target = normalized_postgres_url(restore_url)
    subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl", "--dbname", target, str(backup)],
        check=True,
    )
    import psycopg2
    with psycopg2.connect(target) as db, db.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        tables = {row[0] for row in cursor.fetchall()}
        if not REQUIRED_TABLES.issubset(tables):
            raise RuntimeError("restored PostgreSQL database is missing required tables")
        cursor.execute("SELECT count(*) FROM tenants")
        tenant_count = cursor.fetchone()[0]
    return {"integrity": "structural checks passed", "tenant_count": tenant_count}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--restore-url")
    args = parser.parse_args()
    backup = args.backup.resolve()
    manifest = verify_manifest(backup)
    result = verify_sqlite(backup) if manifest["kind"] == "sqlite" else verify_postgres(backup, args.restore_url)
    print(json.dumps({"verified": True, "backup": backup.name, **result}))


if __name__ == "__main__":
    main()
