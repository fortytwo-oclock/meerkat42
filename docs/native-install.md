# Native (Non-Docker) Hub Installation

Meerkat42 runs natively on any Linux server, VM, Raspberry Pi, or cloud host
without requiring Docker. You get the exact same Hub engine, SQLite or
PostgreSQL/Redis backend, offline Ed25519 licensing, and real-time WebSocket
ingestion.

## Prerequisites

- **Linux with `systemd`** (Ubuntu 22.04/24.04, Debian 12+, Rocky/RHEL 9+, Fedora, Arch)
- **Python 3.11+** (`python3`, `python3-venv`)
- **Node.js 20+ & `npm`** (only needed if you also install the Next.js dashboard via `--with-frontend`)

## Quick Install (SQLite Self-Hosted)

```bash
git clone https://github.com/fortytwo-oclock/meerkat42.git
cd meerkat42

# Preview what will be installed first (optional)
bash scripts/install-hub.sh --with-frontend --dry-run

# Install Hub + Web Dashboard as hardened systemd services
sudo bash scripts/install-hub.sh --with-frontend
```

This creates:
- Application files and isolated venv in `/opt/meerkat42`
- Persistent SQLite database in `/var/lib/meerkat42/hub.db`
- Locked-down (`0600`) secrets & configuration in `/etc/meerkat42/hub.env`
- Systemd services `meerkat42-hub.service` (port `8080`) and `meerkat42-web.service` (port `3000`)

Grab your generated operator token:

```bash
sudo grep '^ADMIN_TOKEN=' /etc/meerkat42/hub.env
```

## Cloud / Multi-Replica Native Install (PostgreSQL + Redis)

```bash
sudo bash scripts/install-hub.sh \
  --mode postgres \
  --database-url "postgresql+asyncpg://meerkat:secret@db.internal:5432/meerkat42" \
  --redis-url "redis://redis.internal:6379/0" \
  --bind 0.0.0.0 \
  --port 8080
```

Place Caddy, Nginx, or a cloud load balancer in front to terminate TLS and proxy
`/api/*` (with WebSocket upgrade headers) to the Hub. See
[External access](external-access.md).

## Single-Process Static UI Mode

If you export or build a static dashboard bundle and want a single process on
one port (no Node.js runtime in production), set `STATIC_FRONTEND_DIR` in
`/etc/meerkat42/hub.env`:

```env
STATIC_FRONTEND_DIR=/opt/meerkat42/static-ui
```

The Hub mounts the static directory at `/` after `/api/*` and WebSocket routes.

## Managing Services & Upgrading

```bash
sudo systemctl status meerkat42-hub meerkat42-web
sudo journalctl -u meerkat42-hub -f
```

To upgrade in place (keeps `/etc/meerkat42/hub.env` and `/var/lib/meerkat42` untouched):

```bash
git pull
sudo bash scripts/install-hub.sh --with-frontend
```

## Uninstall Services

```bash
sudo bash scripts/install-hub.sh --uninstall
```

This stops and removes the systemd units while preserving your database and
configuration files until you delete them manually.
