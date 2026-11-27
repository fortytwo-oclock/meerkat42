#!/usr/bin/env python3
"""Cross-build reproducible edge-agent binaries and a checksum manifest."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess


TARGETS = {
    "linux-amd64": {"GOOS": "linux", "GOARCH": "amd64"},
    "linux-386": {"GOOS": "linux", "GOARCH": "386"},
    "linux-arm64": {"GOOS": "linux", "GOARCH": "arm64"},
    "linux-armv7": {"GOOS": "linux", "GOARCH": "arm", "GOARM": "7"},
    "linux-armv6": {"GOOS": "linux", "GOARCH": "arm", "GOARM": "6"},
    "darwin-amd64": {"GOOS": "darwin", "GOARCH": "amd64"},
    "darwin-arm64": {"GOOS": "darwin", "GOARCH": "arm64"},
    "windows-amd64": {"GOOS": "windows", "GOARCH": "amd64"},
    "windows-arm64": {"GOOS": "windows", "GOARCH": "arm64"},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("dist/agents"), help="artifact directory"
    )
    parser.add_argument(
        "--go", dest="go_binary", help="Go executable (default: resolve go from PATH)"
    )
    parser.add_argument(
        "--target",
        action="append",
        choices=sorted(TARGETS),
        help="build only this target (repeatable; default: all)",
    )
    args = parser.parse_args()

    go = args.go_binary or shutil.which("go")
    if go is None:
        parser.error("go was not found on PATH")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = args.target or list(TARGETS)
    manifest: list[str] = []

    for target in selected:
        suffix = ".exe" if TARGETS[target]["GOOS"] == "windows" else ""
        destination = output_dir / f"control-plane-agent-{target}{suffix}"
        temporary = destination.with_suffix(".tmp")
        env = os.environ.copy()
        env.update(TARGETS[target])
        env["CGO_ENABLED"] = "0"
        print(f"building {target} -> {destination}", flush=True)
        try:
            subprocess.run(
                [
                    go,
                    "build",
                    "-trimpath",
                    "-ldflags=-s -w",
                    "-o",
                    str(temporary),
                    "./cmd/agent",
                ],
                check=True,
                env=env,
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        manifest.append(f"{sha256(destination)}  {destination.name}")

    manifest_path = output_dir / "SHA256SUMS"
    manifest_path.write_text("\n".join(manifest) + "\n", encoding="ascii", newline="\n")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
