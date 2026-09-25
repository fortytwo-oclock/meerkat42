#!/usr/bin/env bash
# Back up the Hub's PostgreSQL database from inside its Docker container.
#
# Same artefacts as scripts/hub_backup.py (hub-<stamp>.dump in pg_dump custom format plus a
# .manifest.json sidecar), so scripts/verify_hub_restore.py can drill-restore them. Use this
# when the host has no pg_dump or no route to the database network, e.g. the single-host
# cloud stack where postgres is only on the internal "backend" network.
#
#   scripts/hub_backup_docker.sh --container <postgres container> --output-dir DIR [--keep N]
set -euo pipefail

container="" output_dir="" keep=0
while (($#)); do
  case "$1" in
    --container) container=$2; shift 2 ;;
    --output-dir) output_dir=$2; shift 2 ;;
    --keep) keep=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n $container && -n $output_dir ]] || { echo "usage: $0 --container NAME --output-dir DIR [--keep N]" >&2; exit 2; }

mkdir -p "$output_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest="$output_dir/hub-$stamp.dump"
tmp=$(mktemp "$output_dir/.hub-backup-XXXXXX")
trap 'rm -f "$tmp"' EXIT

# Credentials come from the container's own environment; nothing is printed.
docker exec "$container" sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" --format=custom --no-owner --no-acl' > "$tmp"
[[ -s $tmp ]] || { echo "pg_dump produced no output" >&2; exit 1; }
mv "$tmp" "$dest"
chmod 600 "$dest"

python3 - "$dest" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

dest = Path(sys.argv[1])
h = hashlib.sha256()
with dest.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
manifest = {
    "version": 1,
    "kind": "postgresql",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "filename": dest.name,
    "size": dest.stat().st_size,
    "sha256": h.hexdigest(),
}
Path(str(dest) + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"backup": str(dest), "sha256": manifest["sha256"], "size": manifest["size"]}))
PY

if ((keep > 0)); then
  find "$output_dir" -maxdepth 1 -name 'hub-*.dump' | sort | head -n -"$keep" |
    while IFS= read -r old; do rm -f -- "$old" "$old.manifest.json"; done
fi
