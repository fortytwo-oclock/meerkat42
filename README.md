# Meerkat42

**Every node. One watch.**

Meerkat42 is a self-hosted monitoring and operations platform for homelabs,
small infrastructure teams, and managed service providers. It gives you one
place to see Linux hosts, containers, storage, alerts, configuration backups,
and approved remote actions.

Each monitored machine runs a small agent that connects outward to the Hub.
You do not need to open a management port at every remote site.

## What it does

- **Fleet monitoring** — CPU, memory, load, Docker containers, ZFS
  pools, SMART disks, and optional Proxmox guests.
- **Useful alerting** — stateful alerts with recovery notifications, flap
  suppression, muting, and delivery through Discord, Slack, SMTP, or webhooks.
- **Configuration recovery** — selected Compose and environment files are
  encrypted on the agent before the Hub stores them.
- **Governed operations** — allowlisted remote actions, durable command state,
  actor attribution, optional two-owner approval, and time-limited emergency
  diagnostics.
- **Client separation** — owner, administrator, and scoped viewer roles for
  operators managing more than one client or environment.
- **Flexible deployment** — SQLite for a small self-hosted installation, or
  PostgreSQL and Redis for larger and multi-replica deployments.

## How it fits together

```text
 Remote Linux host                         Meerkat42 headend
┌──────────────────┐    outbound WS/WSS   ┌────────────────────┐
│ Meerkat42 Agent  │ ───────────────────▶ │ Hub API            │
│                  │                      │                    │
│ Linux + Docker   │ ◀─────────────────── │ policy + commands  │
│ ZFS + SMART      │   approved actions   │ alerts + history   │
│ optional Proxmox │                      └─────────┬──────────┘
└──────────────────┘                                │
                                      ┌─────────────┴──────────┐
                                      │ Browser dashboard      │
                                      └────────────────────────┘
```

Agents initiate the connection and reconnect automatically. The Hub stores
telemetry and policy state. The dashboard talks to the Hub through a same-origin
API proxy.

## Technology

| Area | What Meerkat42 uses |
|---|---|
| Edge agent | Go, static Linux binaries, WebSocket transport |
| Hub | Python 3.11, FastAPI, Pydantic, SQLAlchemy 2, Alembic |
| Dashboard | Next.js, React, TypeScript, Tailwind CSS |
| Small-install storage | SQLite with time-series rollups and retention |
| Larger-install storage | PostgreSQL plus optional Redis coordination |
| Packaging | Docker, Docker Compose, multi-architecture BuildKit builds |
| Security | Outbound-only agents, AES-256-GCM Vault backups, Ed25519 offline licenses, RBAC, TOTP grants |
| Notifications | Discord, Slack, SMTP, and generic HTTPS webhooks |

## Choose a deployment

| If you are… | Start here |
|---|---|
| Trying Meerkat42 on one computer | [Self-hosted quickstart](#quickstart) |
| Running the headend on a Raspberry Pi 4/5 | [Raspberry Pi guide](docs/platform-support.md#raspberry-pi-45-headend) |
| Monitoring an older Pi or 32-bit machine | [Agent platform matrix](docs/platform-support.md#support-matrix) |
| Building a larger production installation | [PostgreSQL and Redis deployment](deploy/README.md#cloud-deployment) |

## Quickstart

Install Git, Docker Engine or Docker Desktop, Docker Compose v2, and Python 3.10
or newer. Then run:

```bash
git clone https://github.com/fortytwo-oclock/meerkat42.git
cd meerkat42
python scripts/bootstrap_self_host.py
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml up -d --build
```

Open <http://localhost:3000>. Under **Settings**, paste the `ADMIN_TOKEN` from
`deploy/self-host.env` into **Operator token**. The interactive Hub API reference
is available at <http://localhost:8080/docs>.

The default installation binds to localhost and stores its data in a Docker
volume. It does not expose the Hub to the internet.

To connect a monitored machine, continue with
[Connect an agent](docs/getting-started.md#connect-an-agent). The complete guide
covers LAN access, TLS, verification, updates, troubleshooting, and removal.

## Platform support

The complete Hub and dashboard support Linux x86-64 and ARM64. A Raspberry Pi 4
with at least 4 GB of RAM or a Pi 5 can run the headend with a 64-bit OS and
SSD-backed storage.

Native agents are built for:

- Linux x86-64 and x86 32-bit
- Linux ARM64
- Linux ARMv7, including Pi 2 and 32-bit Pi 3/4 installations
- Linux ARMv6, including Pi 1 and Pi Zero

See [Platform support](docs/platform-support.md) for limitations, native
installation, container builds, and the hardware acceptance checklist.

## Documentation

| Task | Guide |
|---|---|
| Install and connect the first node | [Getting started](docs/getting-started.md) |
| Deploy on Raspberry Pi or ARM | [Platform support](docs/platform-support.md) |
| Reach the dashboard from outside | [External access](docs/external-access.md) |
| Back up and restore the Hub | [Disaster recovery](docs/disaster-recovery.md) |
| Run complete acceptance testing | [Test environment](docs/test-environment.md) |
| Review deployment topologies | [Deployment guide](deploy/README.md) |
| Work with the API and Hub | [Hub reference](hub/README.md) |
| Develop the dashboard | [Dashboard reference](frontend/README.md) |

The [documentation index](docs/README.md) separates operator tutorials,
runbooks, and developer references.

## Security notes

The quickstart is private by default. Before allowing access from another
network, put the Hub behind a TLS reverse proxy, restrict it with a firewall,
set the exact dashboard origin, and use `wss://` for agents.

The Docker socket is powerful even when mounted read-only. Run agents only on
machines you administer, and enable optional device access, Vault paths, or
Proxmox credentials only where they are required.

## Project status

Meerkat42 is under active development. The repository includes automated tests
for the Go agent, Hub, database migrations, dashboard build, Compose profiles,
restore workflow, and supported agent architectures. Hardware-specific features
still need validation on the exact systems where they will run.
