# Hub disaster recovery runbook

Owner: platform operations. Review quarterly and after every real restore or drill.

## Inventory and targets

| State | Tier | Target RPO | Target RTO | Protection |
|---|---:|---:|---:|---|
| PostgreSQL or SQLite Hub database | 1 | 1 hour | 4 hours | Hourly dump/snapshot; encrypted off-account copy |
| Vault ciphertext inside the Hub database | 1 | 1 hour | 4 hours | Same database backup; `VAULT_KEY` stored separately |
| Signing keys, Stripe/SMTP/agent secrets | 1 | On change | 4 hours | Versioned secret manager with separate recovery access |
| Code, deployment config, container images | 2 | 24 hours | 8 hours | Git mirror plus immutable release images |
| Redis leases/pub-sub and derived metric rollups | 3 | None | 15 minutes | Rebuilt automatically; do not restore Redis |

These are cold-restore targets for the included single-host cloud topology, not
claims of automatic failover. A sub-hour RTO requires managed multi-zone database
failover and a separately deployed application standby.

Use a 3-2-1 layout: the live database, an encrypted backup in a second storage
system, and an immutable/off-account copy. Retain hourly backups for 48 hours,
daily backups for 35 days, and monthly backups for 12 months unless a stricter
contract or regulation applies.

## Backup

Run hourly from a scheduler outside the application host when possible:

```bash
python scripts/hub_backup.py --output-dir /secure-staging/control-plane
```

Upload both the backup and its `.manifest.json` sidecar to encrypted, versioned,
immutable storage. Never keep the only copy beside the live database. Back up the
Vault decryption key and license-signing key through the secret manager's own
export/recovery process; never place those keys beside ciphertext backups.

## Restore decision and prerequisites

Restore for confirmed loss/corruption, an unrecoverable host, or a disaster drill.
An owner must authorize a real restore and record the selected recovery point.
Before starting: stop writes, preserve the damaged database for forensics, verify
the manifest checksum, prepare an isolated target, and start an RTO timer.

## Drill and restore

SQLite performs an isolated full copy plus integrity and schema checks:

```bash
python scripts/verify_hub_restore.py /backups/hub-YYYYMMDDTHHMMSSZ.sqlite3
```

For PostgreSQL, create an empty disposable database whose name ends in
`_restore_drill`. The command intentionally refuses any other database name:

```bash
python scripts/verify_hub_restore.py /backups/hub-YYYYMMDDTHHMMSSZ.dump \
  --restore-url postgresql://user:password@host/control_plane_restore_drill
```

After a successful drill, run the Hub against the isolated target, verify tenant
and node counts, inspect several alerts/backups/audit events, and test an offline
Vault decrypt. For a real restore, switch application traffic only after those
checks pass. Monitor error rate, ingest, command backlog, notification delivery,
and database latency for at least one hour.

If validation fails, do not overwrite the failed target again. Preserve logs,
select the preceding recovery point, and repeat in a new isolated target.

## Drill schedule and log

- Monthly: checksum and SQLite restore or `pg_restore --list` automation.
- Quarterly: isolated full database restore and operator tabletop.
- Annually and before major launches: full application recovery exercise.

| Date | Type | Backup age (actual RPO) | Actual RTO | Outcome/issues |
|---|---|---:|---:|---|
| _Not yet run_ | — | — | — | Record the first operator-run drill here. |

## Incident communication template

> The Control Plane is unavailable while we recover durable state from the most
> recent verified backup. Current estimated recovery point: [time]. Next update:
> [time]. Agent monitoring may buffer or reconnect automatically; remote actions
> are suspended until database integrity is confirmed.
