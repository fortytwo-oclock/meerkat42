# Getting started

This tutorial installs a private Meerkat42 Hub and dashboard, then connects one
Linux machine as an agent. It uses SQLite and is appropriate for evaluation,
homelabs, and small fleets.

## What you need

- A Linux host, macOS computer, or Windows computer with Linux containers.
- Git, Docker Engine or Docker Desktop, and Docker Compose v2.
- Python 3.10 or newer to generate the initial credentials.
- At least 2 GB of free memory and 5 GB of free disk space.

For a Raspberry Pi headend, use a Pi 4 with at least 4 GB of RAM or a Pi 5,
64-bit Raspberry Pi OS, and SSD-backed storage. Then follow the
[Pi-specific section](platform-support.md#raspberry-pi-45-headend).

## Install the Hub and dashboard

```bash
git clone https://github.com/fortytwo-oclock/meerkat42.git
cd meerkat42
python scripts/bootstrap_self_host.py
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml up -d --build
```

PowerShell uses the same commands; put the final `docker compose` command on one
line or use PowerShell's backtick instead of the Bash backslash.

Wait for both services:

```bash
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml ps
curl http://localhost:8080/api/v1/ready
```

The Hub should report healthy and the dashboard should remain running. Open
<http://localhost:3000>, select **Settings**, paste the `ADMIN_TOKEN` value from
`deploy/self-host.env` into **Operator token**, and save it. Treat that token as
an owner credential.

## Connect an agent

On the Linux machine you want to monitor, clone the repository and create the
agent configuration:

```bash
git clone https://github.com/fortytwo-oclock/meerkat42.git
cd meerkat42
cp deploy/agent.env.example deploy/agent.env
```

Edit `deploy/agent.env`:

```dotenv
HUB_URL=ws://192.0.2.10:8080/api/v1/agent/stream
NODE_ID=server-room-01
AUTH_TOKEN=paste_the_AGENT_BOOTSTRAP_TOKEN_from_the_Hub
CLIENT_GROUP=default
PUSH_INTERVAL_SEC=10
```

Replace `192.0.2.10` with the Hub address. Every node needs a stable, unique
`NODE_ID`. Start the agent:

```bash
docker compose -f deploy/docker-compose.agent.yml up -d --build
docker compose -f deploy/docker-compose.agent.yml logs -f agent
```

If the agent and the self-hosted Hub run from this same checkout on the same
Docker host, use `HUB_URL=ws://hub:8080/api/v1/agent/stream`. Both Compose files
share the `deploy_default` network, so the agent must use the Hub service name;
`127.0.0.1` inside the agent container refers to the agent itself.

The agent publishes no inbound ports. It mounts the host's `/proc`, `/sys`, and
Docker socket read-only so it can collect host and container telemetry. The node
should appear in the dashboard within 30 seconds.

To allow audited container controls for selected services, add their exact
Docker names to the agent environment and recreate the agent:

```dotenv
DOCKER_ACTION_CONTAINERS=web,worker,reverse-proxy
```

An empty or missing allowlist disables Restart and Stop at the agent, even if a
Hub administrator requests them. Image updates are pull-only: Meerkat42 does
not recreate a Compose service automatically because doing so without its
deployment file can lose service configuration. Restart and Stop are recorded
as destructive commands and follow the Hub's approval policy.

For production enrollment, create a group-bound, expiring agent token in
**Settings** and use the returned one-time `cpa_...` value instead of sharing the
bootstrap token with additional nodes.

## Reach the Hub from another machine

The generated configuration binds to `127.0.0.1`. For a trusted lab LAN, edit
`deploy/self-host.env`:

```dotenv
BIND_ADDRESS=0.0.0.0
CORS_ORIGINS=http://192.0.2.10:3000
```

Recreate the services and allow ports 3000 and 8080 only from the intended LAN
in the host firewall. For internet or untrusted-network access, keep the
services private behind a TLS reverse proxy, set the exact HTTPS dashboard
origin, and use a `wss://` agent URL. Never expose an unencrypted Hub directly
to the internet. Follow [External dashboard access](external-access.md) for
complete Pangolin, Caddy, and Nginx Proxy Manager instructions.

## Update

Review release notes and back up the Hub before an update:

```bash
python scripts/hub_backup.py --output-dir ./backups
git pull --ff-only
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml up -d --build
```

Update an agent from its checkout with `git pull --ff-only`, followed by:

```bash
docker compose -f deploy/docker-compose.agent.yml up -d --build
```

## Stop or remove

Stop the Hub while preserving its database:

```bash
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml down
```

To remove the containers and permanently delete the named database volume, add
`--volumes`. Back up first; volume deletion cannot be undone through Meerkat42.

Stop an agent with:

```bash
docker compose -f deploy/docker-compose.agent.yml down
```

## Troubleshooting

- **Compose says a variable is missing:** run
  `python scripts/bootstrap_self_host.py`, or pass `--force` only when you intend
  to replace the existing owner and agent credentials.
- **Dashboard loads but mutations fail:** save `ADMIN_TOKEN` as the Operator
  token under Settings.
- **Agent receives 401 or repeatedly reconnects:** confirm `AUTH_TOKEN` exactly
  matches the Hub bootstrap token or an active enrollment token.
- **Agent never appears:** confirm the agent can reach the Hub URL, that the URL
  ends in `/api/v1/agent/stream`, and that a reverse proxy supports WebSockets.
- **Docker or host metrics are empty:** run the agent on Linux and confirm it can
  read the mounted Docker socket, `/proc`, and `/sys`.
- **Hub does not become healthy:** inspect
  `docker compose --env-file deploy/self-host.env -f deploy/docker-compose.hub.yml logs hub`.

For a production-shaped acceptance test, continue with the
[disposable test environment](test-environment.md). For restore procedures, see
[disaster recovery](disaster-recovery.md).
