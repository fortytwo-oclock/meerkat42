# Control Plane Hub

FastAPI hub: WebSocket telemetry ingestion, persistence, alerting, time-series
retention, multi-tenant REST API.

## Layout

```
hub/
  config.py          Settings (DATABASE_URL, retention windows, watchdog timings)
  db/                Async engine + session factory; SQLite/Postgres switch, pragmas
  models/            SQLAlchemy 2.0 models: tenants, client_groups, nodes,
                     license_records, alert_rules, dispatched_alerts, metric_points,
                     metric_rollups
  repositories/      Data-access layer — routes & WS handlers call these, not the ORM
  timeseries/        MetricStore ABC + SqlMetricStore (raw + 1m/5m rollups + prune)
  services/          alerting.py (rule evaluation), liveness.py (watchdogs)
  license_manager.py Offline Ed25519 verification, re-verified on load from DB
  billing/            Stripe signature verification + Ed25519 license issuance
  api/v1/            REST routers: health, license, groups, nodes, alerts, metrics
  ws/                ingest.py (agent stream), ui.py (browser push), bus.py (fan-out)
  migrations/        Alembic (0001_baseline materialises from model metadata)
  main.py            App factory + lifespan (migrate, seed, start watchdogs)
```

## Persistence

| Mode | `DATABASE_URL` |
|------|----------------|
| Self-hosted (default) | `sqlite+aiosqlite:///./data/hub.db` |
| Cloud | `postgresql+asyncpg://user:pass@host/db` |

`JSON` columns become `JSONB` on Postgres. Timestamps are timezone-aware.
On startup the hub runs `alembic upgrade head` (set `AUTO_MIGRATE=false` to skip),
falling back to `create_all` for a fresh DB.

## Time-series retention

Raw 10s samples for 24h → 1m rollups for 7d → 5m rollups for 30d. The
`rollup_loop` background task aggregates every 60s; `prune` runs hourly. Range
selection in `SqlMetricStore.query`: ≤6h raw, ≤7d 1m, else 5m.

Swap the whole store (VictoriaMetrics / Timescale) by implementing `MetricStore`
and assigning `app.state.metric_store` in `main.py` — ingestion and the API are
storage-agnostic.

## Auth & RBAC

- `AGENT_BOOTSTRAP_TOKEN` — shared secret the edge agent sends as `X-Auth-Token`;
  the WS ingest handler rejects mismatches with close code `4401`. Node identity
  comes from the handshake and is **pinned** — later frames cannot reassign it.
- **Principals** (`hub/deps.py`): a request is authenticated via
  `Authorization: Bearer <token>` / `X-Admin-Token` against `ADMIN_TOKEN` (→ `owner`)
  or the `api_tokens` table (`owner` / `admin` / `viewer`). With nothing
  configured the hub runs in open single-operator mode (`owner`, "anonymous").
- `require_role(...)` gates endpoints: mutations need `admin`, token management
  needs `owner`. A `viewer` token may be scoped to one `client_group`
  (`viewer_scope` dependency) and then sees only that group's nodes and alerts;
  cross-scope IDs return 404. `acknowledged_by` is always the principal label.
- All node-addressed reads are tenant-scoped.

## Infrastructure Vault (Phase 4)

`POST /api/v1/nodes/{id}/backup` sends `request_config_backup` down the node's live
tunnel. The agent encrypts each compose/env file (AES-256-GCM, per-file 12-byte
nonce, AAD = relative path) under its `VAULT_KEY` and returns a `command_result`
frame; the ingest handler persists it to `config_backups` as ciphertext. The hub
cannot decrypt. `GET /api/v1/backups/{id}` returns the encrypted blob; restore with
`python scripts/vault_decrypt.py --key "$VAULT_KEY" --in backup.json --out ./restored`.
Feature-gated on `vault_backup`; `VAULT_MAX_BACKUP_BYTES` caps upload size.

## Run locally

```bash
pip install -r hub/requirements.txt
uvicorn hub.main:app --reload --port 8080
# docs at http://localhost:8080/docs
```

## Key endpoints

- `GET  /api/v1/nodes` `?client_group=<slug>` — fleet matrix
- `GET  /api/v1/nodes/{id}` — detail incl. latest telemetry snapshot
- `GET  /api/v1/nodes/{id}/metrics?metric=cpu_usage_pct&range_hours=24`
- `GET  /api/v1/alerts` / `POST /api/v1/alerts/{id}/ack`
- `GET/POST/DELETE /api/v1/alert-rules`
- `GET/POST/DELETE /api/v1/groups`
- `GET/POST /api/v1/license` — offline verify + persist
- `POST /api/v1/billing/stripe/webhook` — verified, idempotent subscription events
- `GET  /api/v1/billing/plans` — configured plans safe for dashboard display
- `POST /api/v1/billing/checkout` — owner-only hosted subscription checkout
- `POST /api/v1/billing/portal` — owner-only Stripe Billing Portal session
- `GET  /api/v1/billing/subscription` — owner-only subscription status
- `GET  /api/v1/billing/license` — owner-only generated offline license download
- `POST /api/v1/billing/license/retry` — owner-only email delivery retry
- `GET/POST/PATCH/DELETE /api/v1/channels` — notification channels
  (`discord` / `slack` / `smtp` / `webhook`); `POST .../{id}/test`, `.../{id}/mute`
- `WS   /api/v1/agent/stream` — agent ingestion (unchanged protocol)
- `WS   /api/v1/ui/stream` — live telemetry/alert push to dashboards

## Stripe billing automation (Phase 5)

The webhook consumes `customer.subscription.created`, `.updated`, and
`.deleted`. Each Stripe subscription must include `metadata.tenant_slug`; the
optional `customer_name` and `customer_email` metadata fields are retained for
delivery. Entitlements are mapped by Stripe product ID, not price ID.

Configure the billing service with:

```bash
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_SECRET_KEY=sk_live_...
LICENSE_SIGNING_PRIVATE_KEY=<base64 raw 32-byte Ed25519 private key>
ED25519_PUBLIC_KEY=<matching base64 public key>
STRIPE_PRODUCT_PLANS_JSON='{"prod_enterprise":{"price_id":"price_enterprise","tier":"enterprise","max_nodes":100,"features":["vault_backup","white_label"]}}'
ADMIN_TOKEN=<required for billing status and license download>
REDIS_URL=redis://redis:6379/0
BILLING_SUCCESS_URL=https://monitor.example.com/settings?checkout=success
BILLING_CANCEL_URL=https://monitor.example.com/settings?checkout=canceled
BILLING_PORTAL_RETURN_URL=https://monitor.example.com/settings
BILLING_EMAIL_ENABLED=true
BILLING_EMAIL_FROM=licenses@example.com
BILLING_SMTP_HOST=smtp.example.com
BILLING_SMTP_PORT=587
BILLING_SMTP_USERNAME=...
BILLING_SMTP_PASSWORD=...
```

The private key belongs only on the hosted billing service. Customer hubs keep
the public key and verify delivered licenses entirely offline. Webhook responses
never contain license tokens.

`POST /api/v1/billing/checkout` creates a hosted subscription Checkout session;
`POST /api/v1/billing/portal` creates an on-demand Stripe Billing Portal session.
Both are owner-only, use server-configured redirect URLs, and forward an
idempotency key to Stripe. The dashboard exposes both flows under Settings.

When billing email is enabled, issuance writes a durable outbox record in the
same transaction as the license. A background worker delivers it through SMTP
with bounded exponential backoff. SMTP outages therefore never make Stripe
webhooks fail or lose a license. The Settings page exposes delivery state and a
manual retry; persisted errors contain only the exception class, never the token,
SMTP response, or credentials. Delivery is at-least-once and uses a deterministic
`Message-ID` so downstream mail systems can deduplicate a retry after a crash.

## Multi-replica realtime routing

Set `REDIS_URL` when running more than one Hub replica. Each live agent holds an
expiring Redis ownership lease. Commands received by any replica route to the
replica that owns the socket, and UI events fan out across every replica. Lease
compare-and-delete prevents a slow disconnect from removing a newer connection.
If Redis is omitted, the exact same interface stays in process for zero-config
self-hosting.

The cloud Compose stack starts a one-shot migration/seed service before two Hub
replicas, avoiding concurrent Alembic upgrades and bootstrap races. Alerting,
rollups, liveness, and email delivery run in one dedicated worker so scaling the
API cannot duplicate background work. Caddy terminates TLS and balances WebSocket
connections through Docker's service network. A multi-host deployment
should point the same stack at managed PostgreSQL and Redis and place a global
WebSocket-capable load balancer in front; no session affinity is required.

## Alert dispatch (Phase 3)

Background `dispatch_loop` picks up `AlertEvent`s and delivers to matching
channels (`hub/notify/`): FIRING once (`dispatched_at`), then RESOLVED once
(`resolve_notified_at`). A re-fire of an identical alert within
`ALERT_FLAP_WINDOW_SECONDS` of its resolve is created but `suppressed` (shown on
the dashboard, not re-notified). Per-channel `CHANNEL_MIN_INTERVAL_SECONDS`
throttles FIRING bursts; RESOLVED always goes out. Channels have
`min_severity`, `enabled`, `muted_until`. Add a transport by subclassing
`Sender` in `hub/notify/base.py`.
