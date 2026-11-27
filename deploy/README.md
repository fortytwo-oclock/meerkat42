# Deployment guide

Choose the smallest topology that meets the job:

| Deployment | Use it for | Start with |
|---|---|---|
| Self-hosted SQLite | Evaluation, homelab, small fleet | [`docs/getting-started.md`](../docs/getting-started.md) |
| Raspberry Pi headend | Small fleet on a Pi 4/5 | [`docs/platform-support.md`](../docs/platform-support.md) |
| PostgreSQL and Redis | Larger or hosted installation | `docker-compose.cloud.yml` below |

To publish a self-hosted or Pi dashboard, follow the
[external access guide](../docs/external-access.md). It includes Pangolin,
Caddy, Nginx Proxy Manager, WebSocket routing, and firewall guidance.

The Compose files build images from the checked-out source. Published release
images can replace those builds later without changing the topology. Keep local
environment files out of Git; every real template has an ignored counterpart.

## Cloud deployment

`docker-compose.cloud.yml` is the production-oriented single-host topology:

- Caddy terminates TLS and proxies API/WebSocket traffic.
- Two Hub replicas serve HTTP and long-lived sockets.
- Redis fans UI events out and routes commands to the replica that owns an agent.
- One worker runs liveness, rollups, alert dispatch, and license email delivery.
- One-shot bootstrap applies migrations and seeds defaults before services start.
- PostgreSQL stores durable tenant, telemetry, alert, Vault, and billing state.

Generate secure core credentials (Stripe stays disabled until configured), or
copy `cloud.env.example` and replace every placeholder:

```bash
python scripts/bootstrap_cloud.py --domain monitor.example.com
```

Point the domain's DNS at the host, then run:

```bash
docker compose --env-file deploy/cloud.env -f deploy/docker-compose.cloud.yml up -d --build
```

When Pangolin, Cloudflare Tunnel, or another external reverse proxy terminates
TLS, use the included overlay instead of publishing Caddy on ports 80/443:

```bash
docker compose --env-file deploy/cloud.env \
  -f deploy/docker-compose.cloud.yml \
  -f deploy/docker-compose.pangolin.yml up -d --build
```

This runs Caddy as a plain-HTTP edge bound to `127.0.0.1:8080`; the dashboard,
Hub, PostgreSQL, and Redis remain private. Run the tunnel connector on the same
host and point its HTTP target at `127.0.0.1:8080`. Caddy forwards `/api`
(including WebSockets) to the Hub and all other requests to the dashboard,
while applying the same security headers as a direct-TLS installation.

For a multi-host or multi-region deployment, use managed PostgreSQL and Redis,
run `python -m hub.bootstrap` once per release, run exactly one
`python -m hub.worker`, and place the Hub replicas behind a WebSocket-capable global load
balancer. Sticky sessions are not required. Keep Redis private; its agent lease
keys and pub/sub channels are internal coordination data.

The included Redis container is intentionally ephemeral because durable state
lives in PostgreSQL. A managed Redis service should provide authentication and
TLS; set `REDIS_URL` accordingly.

Health probes use `/api/v1/ready`, which verifies the database and configured
Redis backplane; `/api/v1/live` is process-only. Prometheus-compatible tenant
metrics are owner-gated at `/api/v1/ops/metrics`.

Production state recovery, retention targets, off-account copy requirements,
and quarterly restore drills are documented in
[`docs/disaster-recovery.md`](../docs/disaster-recovery.md). The backup command
creates an atomic dump and SHA-256 manifest; the verification command restores
into an isolated SQLite database or an explicitly named PostgreSQL
`*_restore_drill` database.

### Single host behind Pangolin

When the Hub runs on a host that already serves ports 80/443 (or has no public IP), let a
Pangolin tunnel carry the traffic:

1. Start the stack with the Pangolin override so Caddy listens on `127.0.0.1:8080` only.
   Use the same `-p`/`-f`/`--env-file` flags every time; a small wrapper script avoids mistakes:

   ```bash
   docker compose -p control-plane --env-file deploy/cloud.env \
     -f deploy/docker-compose.cloud.yml -f deploy/docker-compose.pangolin.yml up -d
   ```
2. Run Newt on the same host: copy `deploy/newt.env.example` to `deploy/newt.env`, fill in the
   site credentials, and `docker compose -f deploy/docker-compose.newt.yml up -d`.
3. In Pangolin, target `127.0.0.1:8080` on that site and add an accept rule for
   `/api/v1/agent/stream`, so agents connect without SSO while people still sign in.

**Moving the Hub to another host** keeps every agent connected without re-enrolment if the
domain stays the same: stop Newt on the old host, stop the stack, copy the `postgres-data`,
`caddy-data` and `caddy-config` volumes while stopped, start the stack on the new host, then
start Newt there. Agents reconnect on their own once their old connection times out (a few
minutes). Only one Newt per site credential may run at a time.

**Backups without host tools:** `scripts/hub_backup_docker.sh` runs `pg_dump` inside the
postgres container and writes the same `hub-<stamp>.dump` + manifest as `hub_backup.py`, so
`verify_hub_restore.py` can drill-restore it. Example systemd units are in `deploy/systemd/`:

```bash
scripts/hub_backup_docker.sh --container control-plane-postgres-1 --output-dir /var/backups/control-plane --keep 7
```

## Raspberry Pi and ARM

`docker-compose.pi.yml` is the small-fleet ARM64 headend profile. It runs the Hub
with SQLite plus the dashboard and is intended for a Pi 4 (4 GB+) or Pi 5 using
a 64-bit OS and SSD-backed storage. Copy `pi.env.example`, replace the token
placeholders, and follow [`docs/platform-support.md`](../docs/platform-support.md).

Older 32-bit Raspberry Pis are supported as edge agents through the ARMv6 and
ARMv7 native builds. The complete dashboard headend is not supported on 32-bit
systems because its maintained Node/Next.js container toolchain is 64-bit only.

## Kubernetes (Helm)

For multi-replica cloud or Enterprise Kubernetes deployments (EKS, GKE, AKS, k3s),
install the included Helm chart at `deploy/helm/meerkat42`:

```bash
kubectl create secret generic meerkat42-secrets \
  --from-literal=DATABASE_URL="postgresql+asyncpg://user:pass@postgres:5432/meerkat42" \
  --from-literal=REDIS_URL="redis://redis:6379/0" \
  --from-literal=ADMIN_TOKEN="$(openssl rand -base64 32)" \
  --from-literal=AGENT_BOOTSTRAP_TOKEN="$(openssl rand -base64 32)"

helm upgrade --install meerkat42 deploy/helm/meerkat42 \
  --set ingress.host=monitor.example.com \
  --set config.corsOrigins=https://monitor.example.com
```

Run `python -m hub.bootstrap` once per release as a pre-upgrade migration job,
keep `worker.enabled=true` (singleton replica), and scale `hub.replicas` freely
behind any WebSocket-capable Ingress controller.
