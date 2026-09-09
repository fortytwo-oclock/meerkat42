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
- `WS   /api/v1/agent/stream` — agent ingestion (unchanged protocol)
- `WS   /api/v1/ui/stream` — live telemetry/alert push to dashboards
