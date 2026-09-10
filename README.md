# Meerkat42

Meerkat42 watches Linux infrastructure from one dashboard. A small Go agent
collects host, Docker, ZFS, SMART, and optional Proxmox telemetry, then opens an
outbound WebSocket connection to the Hub. Remote sites do not need an inbound
management port.

The project includes a FastAPI Hub, a browser dashboard, SQLite for simple
self-hosting, PostgreSQL/Redis for larger deployments, alert delivery, encrypted
configuration backups, scoped access tokens, and governed remote actions.

## Start here

You need Git, Docker Engine or Docker Desktop, Docker Compose v2, and Python 3.10
or newer. On a fresh machine:

```bash
git clone https://github.com/fortytwo-oclock/meerkat42.git
cd meerkat42
python scripts/bootstrap_self_host.py
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml up -d --build
```

Open <http://localhost:3000>. Go to **Settings**, copy `ADMIN_TOKEN` from
`deploy/self-host.env` into **Operator token**, and save it. The Hub API and its
interactive reference are available at <http://localhost:8080/docs>.

Next, [connect your first monitored machine](docs/getting-started.md#connect-an-agent).
The complete tutorial also covers verification, LAN/TLS access, updates,
backups, troubleshooting, and removal.

## Raspberry Pi and ARM

- A Raspberry Pi 4 with 4 GB or more, or a Pi 5, can run the complete headend
  with a 64-bit OS and SSD-backed storage.
- Pi 2, Pi Zero, and 32-bit systems can run the lightweight agent.
- Native agent builds cover Linux amd64, 386, ARM64, ARMv7, and ARMv6.

See [Platform support and Raspberry Pi deployments](docs/platform-support.md)
for the exact matrix and Pi-specific Compose instructions.

## Documentation

| I want to… | Read |
|---|---|
| Install Meerkat42 and connect a node | [Getting started](docs/getting-started.md) |
| Run it on Raspberry Pi or ARM | [Platform support](docs/platform-support.md) |
| Validate a complete deployment | [Test environment](docs/test-environment.md) |
| Back up and restore the Hub | [Disaster recovery](docs/disaster-recovery.md) |
| Understand production topologies | [Deployment guide](deploy/README.md) |
| Work on the Hub or API | [Hub reference](hub/README.md) |
| Work on the dashboard | [Frontend reference](frontend/README.md) |

The [documentation index](docs/README.md) explains which guides are tutorials,
operator runbooks, and developer references.

## Repository layout

| Path | Purpose |
|---|---|
| `cmd/agent`, `pkg/` | Go edge agent and agent-side integrations |
| `hub/` | FastAPI API, persistence, alerting, licensing, and command routing |
| `frontend/` | Next.js operator dashboard |
| `deploy/` | Dockerfiles, Compose profiles, and environment templates |
| `scripts/` | Setup, verification, backup, restore, and release utilities |
| `docs/` | User tutorials and operator runbooks |

## Security defaults

- Agents connect outbound; the agent Compose file publishes no ports.
- The quickstart binds the Hub and dashboard to loopback by default.
- Setup generates independent owner and agent credentials and keeps them in an
  ignored local file.
- Infrastructure Vault encryption happens on the agent; the Hub stores
  ciphertext and never receives the Vault key.
- Emergency diagnostics are disabled by default and never provide an arbitrary
  shell.

Do not expose the Hub directly to the internet. Use TLS, a reverse proxy, a
restricted firewall, and `wss://` agent URLs for any non-local deployment.

## Development checks

```bash
go test ./cmd/... ./pkg/...
go vet ./cmd/... ./pkg/...
python -m unittest discover -s hub/tests -v
cd frontend && npm ci && npm run build
```

CI also validates migrations, Compose profiles, restore tooling, dependency
changes, and all supported agent architectures.
