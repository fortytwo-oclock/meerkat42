# Meerkat42 documentation

Start with one document and follow links only when you need more depth.

## New operators

1. [Getting started](getting-started.md) — install the Hub and dashboard with
   Docker, connect an agent, verify the deployment, update it, or remove it.
2. [Native (non-Docker) install](native-install.md) — straight Linux systemd
   installation of the Hub and dashboard with zero Docker dependency.
3. [Platform support](platform-support.md) — Raspberry Pi, ARM, 32-bit agents,
   native binaries, and hardware acceptance checks.
4. [External access](external-access.md) — publish the dashboard safely with
   Pangolin, Caddy, Nginx Proxy Manager, or a private network.

## Operators

- [Deployment guide](../deploy/README.md) — cloud topology, reverse proxy,
  PostgreSQL, Redis, and deployment boundaries.
- [App Store & Enterprise distribution](store-publishing.md) — publishing the
  unified Flutter app to Microsoft Store, Apple App Store / Mac App Store,
  Google Play, and Enterprise MDM (Intune, ABM, Managed Google Play).
- [Disaster recovery](disaster-recovery.md) — backup targets, restore commands,
  and recovery drills.

## Maintainers and contributors

- [Disposable test environment](test-environment.md) — full-stack acceptance
  testing with PostgreSQL, Redis, Mailpit, an agent, and Vault fixtures.
- [Hub reference](../hub/README.md) — configuration, API areas, persistence,
  authentication, and internal services.
- [Frontend reference](../frontend/README.md) — dashboard development and route
  structure.

User-facing tutorials and runbooks live in `docs/`. Component reference stays
beside its code. Documentation should be checked whenever its related Compose
file, environment setting, endpoint, or release behavior changes.
