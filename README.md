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
  detail (containers / ZFS / SMART, history charts), license modal, live WS.
- **Phase 3 — Alert dispatch** (Discord/Slack/SMTP/webhook), **Phase 4 —
  Vault/RBAC/white-label**, **Phase 5 — Stripe + cloud:** pending.
