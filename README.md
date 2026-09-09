# Distributed Control Plane

Open-core infrastructure monitoring and control plane for MSPs, boutique IT
agencies, and homelabbers. Outbound-only edge agents, offline cryptographic
licensing, self-hosted or cloud.

```
┌────────────┐   wss:// (outbound only)   ┌──────────────┐      ┌─────────────┐
│ Edge Agent │ ─────────────────────────▶ │  Control     │ ◀──▶ │  Dashboard  │
│  (Go)      │   telemetry every 10s      │  Plane Hub   │  WS  │  (Next.js)  │
│ /proc      │ ◀───────────────────────── │  (FastAPI)   │ REST │             │
│ docker.sock│   HubCommand (reverse)     └──────┬───────┘      └─────────────┘
│ ZFS / SMART│                                   │
└────────────┘                          SQLite (self-host) / Postgres (cloud)
                                        + time-series (raw → 1m → 5m rollups)
```

## Components

| Path | What |
|------|------|
| `cmd/agent`, `pkg/telemetry`, `pkg/tunnel` | Go edge agent: `/proc` + Docker + ZFS auto-discovery, reconnecting outbound WebSocket |
| `pkg/license`, `cmd/license-tool`, `scripts/license_tool.py` | Offline Ed25519 signed-JWT licensing (tamper-evident `max_nodes` / `tier` / `exp`) |
| `hub/` | Control plane: WS ingestion, **persistence**, alert rules, **time-series retention**, multi-tenant REST API — see [hub/README.md](hub/README.md) |
| `frontend/` | Next.js + Tailwind + shadcn dashboard — see [frontend/README.md](frontend/README.md) |
| `deploy/` | Dockerfiles + compose (zero inbound ports for agents) |

The agent can optionally ask the local Docker Engine for each tag's current
registry distribution digest and flag containers whose running manifest differs.
Set `IMAGE_UPDATE_CHECK_ENABLED=true`; checks are registry-allowlisted, cached,
and limited to one stale image per telemetry cycle.

## Quickstart (self-hosted)

```bash
# Hub + dashboard (SQLite, zero external deps)
docker compose -f deploy/docker-compose.hub.yml up --build
#   hub   → http://localhost:8080  (/docs)
#   dash  → http://localhost:3000

# Point an agent at it
NODE_ID=lab-01 CLIENT_GROUP="Internal Lab" \
HUB_URL=ws://localhost:8080/api/v1/agent/stream \
  go run ./cmd/agent
```

Local dev without Docker:

```bash
pip install -r hub/requirements.txt && uvicorn hub.main:app --reload   # :8080
cd frontend && npm install && npm run dev                              # :3000
```

## Managed-cloud baseline

`deploy/docker-compose.cloud.yml` provides a production-shaped single-host
deployment with PostgreSQL, Redis, two Hub replicas, a dedicated background
worker, the dashboard, and Caddy for automatic TLS plus same-origin
REST/WebSocket routing.

```bash
cp deploy/cloud.env.example deploy/cloud.env
# Replace every placeholder and point DOMAIN DNS at the host.
docker compose --env-file deploy/cloud.env \
  -f deploy/docker-compose.cloud.yml up -d --build
```

Redis maintains expiring agent ownership leases and routes commands/events across
replicas, so WebSocket session affinity is not required. See
[deploy/README.md](deploy/README.md) for multi-host guidance.

## Editions

| | Community (default) | Enterprise (offline license) | Hosted Cloud |
|-|--|--|--|
| Nodes | 3 | per-license (`-1` = unlimited) | per-plan |
| DB | SQLite | SQLite or Postgres | Postgres (multi-tenant) |
| Client grouping / white-label | – | ✓ (feature claims) | ✓ |

## Roadmap status

- **Phase 1 — Persistence & time-series:** ✅ SQLAlchemy 2.0 models (tenants,
  client_groups, nodes, alert_rules, dispatched_alerts, license_records,
  metric_points, metric_rollups); Alembic; `MetricStore` interface + relational
  store with 10s/1m/5m downsampling and retention pruning; stateful alert events.
- **Phase 2 — Dashboard:** ✅ scaffolded — Fleet Matrix, client-group filter, node
  detail (containers / ZFS / SMART, history charts), license modal, live WS,
  settings page (alert triggers + notification channels).
- **Phase 3 — Alert dispatch:** ✅ Discord / Slack / SMTP / generic webhook
  channels, `MetricStore`-style pluggable senders, FIRING + RESOLVED delivery,
  flapping suppression + per-channel anti-spam throttle, mute, `/test`. Auth
  (`AGENT_BOOTSTRAP_TOKEN`, `ADMIN_TOKEN`) added ahead of schedule.
- **Phase 4 — Enterprise / MSP:** ✅
  - **Infrastructure Vault:** hub issues `request_config_backup` down the tunnel;
    the Go agent (`pkg/vault`) AES-256-GCM encrypts every `docker-compose.y*ml` /
    `*.env` under `VAULT_KEY` (32-byte, base64) and streams ciphertext back. The
    hub stores ciphertext only; restore offline with `scripts/vault_decrypt.py`.
    Gated on the `vault_backup` license feature.
  - **RBAC & client portals:** `api_tokens` table with `owner` / `admin` /
    `viewer` roles; viewer tokens are scoped to one `client_group` and see only
    that group's nodes/alerts. `ADMIN_TOKEN` acts as owner. Token CRUD at
    `/api/v1/tokens` (owner only).
  - **White-labeling:** `/api/v1/branding` serves `white_label_name` / `_logo` /
    `_color` from the license claims; the dashboard shell applies them.
- **Phase 5 — Billing automation + managed cloud:** ✅ Stripe subscription
  webhooks are raw-body/HMAC verified, replay
  bounded, idempotently recorded, and mapped by product to Ed25519 license
  claims. Active/trialing subscriptions issue tenant-bound offline licenses;
  subscription state and issued tokens are durable and owner-gated. Hosted
  Checkout, Stripe Billing Portal sessions, dashboard license retrieval, and a
  PostgreSQL+Caddy cloud deployment are included. Customer email delivery uses
  a durable SMTP outbox with bounded retries. An optional Redis backplane adds
  expiring agent ownership leases, cross-replica reverse-tunnel commands, and UI
  event fan-out; the cloud stack runs migrations/seeding once, starts two Hub
  replicas, and isolates background jobs in one worker to prevent duplicate sends.
- **Phase 6 — Image update intelligence:** ✅ opt-in Docker Engine distribution
  digest checks, immutable digest-pin handling, registry allowlisting, bounded
  refresh work, cached failure retries, fleet/node badges, and an alert-rule
  metric. Authenticated private registries remain an explicit future extension.
- **Phase 7 — Tenant-safe enrollment:** ✅ expiring/revocable enrollment tokens
  bound to one tenant and client group, one-time secret display, last-use audit,
  tenant-specific offline license enforcement, root tenant selection, and
  fail-closed cross-tenant node-ID collision handling.
- **Phase 8 — Audited remote actions:** ✅ durable tenant-scoped command ledger,
  policy allowlist, idempotency keys, offline-agent retries, response timeouts,
  bounded result storage, actor-attributed audit events, cancel/approve APIs, and
  optional two-person approval for destructive actions. The first exposed action
  is a safe agent ping; later integrations extend the same policy boundary.
- **Phase 9 — Proxmox guest control:** ✅ agent-local, TLS-verified Proxmox API
  integration with VM/LXC inventory and start/shutdown/reboot actions. API-token
  credentials never leave the edge, every VMID must be agent-allowlisted, guest
  identity is rechecked against live inventory, and shutdown/reboot use Phase 8's
  optional two-owner approval path.
- **Phase 10 — Update management:** ✅ private-registry digest checks and image
  pulls use an agent-local Docker config via `X-Registry-Auth`; credentials never
  enter telemetry or command results. Pulls require an allowlisted registry and
  an exact image reference already used by a running container. The workflow
  intentionally stages the image without silently recreating workloads.

## Verification

The repository CI workflow validates every component on each push and pull
request: Go unit tests (including the Vault encryption and tunnel command-result
paths), race detection, `go vet`, native and Linux amd64/arm64 agent builds, the
Alembic upgrade/check/downgrade/upgrade cycle, Hub API smoke tests, and the
Next.js production build.

Run the release checks locally with:

```bash
go test ./cmd/... ./pkg/...
go vet ./cmd/... ./pkg/...
go build ./cmd/... ./pkg/...
python -m unittest discover -s hub/tests -v
cd frontend && npm run build
```
