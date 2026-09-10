# Meerkat42 documentation

Start with one document and follow links only when you need more depth.

## New operators

1. [Getting started](getting-started.md) — install the Hub and dashboard,
   connect an agent, verify the deployment, update it, or remove it.
2. [Platform support](platform-support.md) — Raspberry Pi, ARM, 32-bit agents,
   native binaries, and hardware acceptance checks.

## Operators

- [Deployment guide](../deploy/README.md) — cloud topology, reverse proxy,
  PostgreSQL, Redis, and deployment boundaries.
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
