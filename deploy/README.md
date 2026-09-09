# Cloud deployment

`docker-compose.cloud.yml` is the production-oriented single-host topology:

- Caddy terminates TLS and proxies API/WebSocket traffic.
- Two Hub replicas serve HTTP and long-lived sockets.
- Redis fans UI events out and routes commands to the replica that owns an agent.
- One worker runs liveness, rollups, alert dispatch, and license email delivery.
- One-shot bootstrap applies migrations and seeds defaults before services start.
- PostgreSQL stores durable tenant, telemetry, alert, Vault, and billing state.

Copy `cloud.env.example`, replace every placeholder, point the domain's DNS at
the host, then run:

```bash
docker compose --env-file deploy/cloud.env -f deploy/docker-compose.cloud.yml up -d --build
```

For a multi-host or multi-region deployment, use managed PostgreSQL and Redis,
run `python -m hub.bootstrap` once per release, run exactly one
`python -m hub.worker`, and place the Hub replicas behind a WebSocket-capable global load
balancer. Sticky sessions are not required. Keep Redis private; its agent lease
keys and pub/sub channels are internal coordination data.

The included Redis container is intentionally ephemeral because durable state
lives in PostgreSQL. A managed Redis service should provide authentication and
TLS; set `REDIS_URL` accordingly.
