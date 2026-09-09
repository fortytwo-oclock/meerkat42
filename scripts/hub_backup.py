#!/usr/bin/env python3
"""Create an atomic Hub database backup plus a SHA-256 manifest.

SQLite uses the online backup API. PostgreSQL uses pg_dump's custom archive
format. Database URLs are never printed or written to the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalized_postgres_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2"}:
        raise ValueError("not a PostgreSQL URL")
    return urlunsplit(("postgresql", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def sqlite_path(value: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not value.startswith(prefix):
        raise ValueError("not a SQLite URL")
    return Path(value[len(prefix):]).resolve()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def backup(database_url: str, output_dir: Path) -> tuple[Path, dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if database_url.startswith("sqlite+aiosqlite:///"):
        source = sqlite_path(database_url)
        if not source.is_file():
            raise FileNotFoundError("SQLite source database does not exist")
        destination = output_dir / f"hub-{stamp}.sqlite3"
        fd, temporary = tempfile.mkstemp(prefix=".hub-backup-", dir=output_dir)
        os.close(fd)
        try:
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite backup integrity check failed")
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        kind = "sqlite"
    else:
        pg_url = normalized_postgres_url(database_url)
        destination = output_dir / f"hub-{stamp}.dump"
        fd, temporary = tempfile.mkstemp(prefix=".hub-backup-", dir=output_dir)
        os.close(fd)
        try:
            subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", temporary, pg_url],
                check=True,
            )
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        kind = "postgresql"
    manifest = {
        "version": 1,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "filename": destination.name,
        "size": destination.stat().st_size,
        "sha256": digest(destination),
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/hub.db"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination, manifest = backup(args.database_url, args.output_dir.resolve())
    print(json.dumps({"backup": str(destination), "sha256": manifest["sha256"], "size": manifest["size"]}))


if __name__ == "__main__":
    main()
