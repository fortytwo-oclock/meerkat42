# Meerkat42 Licensing & Enterprise Edition

Meerkat42 uses a **Feature-Gated Open Core** licensing model.

## Community Edition (Open Source)

Unless explicitly noted otherwise inside a directory or file header, all source
code in this repository — including the Hub engine (`hub/`), Edge Agent
(`cmd/agent/`, `pkg/`), Web Dashboard (`frontend/`), deployment manifests
(`deploy/`), and scripts (`scripts/`) — is licensed under the
**Apache License, Version 2.0** (see [`LICENSE`](LICENSE)).

Community Edition is free to self-host on local servers, homelabs, or cloud
infrastructure with **unlimited monitored nodes and devices** and includes:

- Real-time server, workstation, and mobile telemetry ingestion
- Live-tail metric streaming and 30-day rollup history
- Fleet alerting and notification channels (Discord, Slack, SMTP, webhooks)
- Basic device check-in and allowlisted remote operations
- Docker Compose and straight native (non-Docker) Hub installation

## Enterprise Edition

Enterprise features — located under `hub/ee/` when present or activated via a
cryptographically signed offline Ed25519 Meerkat42 Enterprise license — require
a commercial Meerkat42 Enterprise subscription. Enterprise capabilities include:

- **Identity & Access:** OIDC / SAML SSO, SCIM provisioning, multi-tenant Spaces
- **Fleet & Device Governance:** MDM zero-touch store distribution (Microsoft
  Intune, Apple Business Manager, Managed Google Play), continuous location
  tracking & geofencing alerts
- **Compliance & Security:** Encrypted Configuration Vault (`vault_backup`),
  enforced two-owner approval workflows, SIEM audit log export
- **Scale & Branding:** Long-term metric retention / TimescaleDB adapters,
  white-label branding (`white_label`), and commercial SLA support

Offline Enterprise licenses work in fully air-gapped environments and are
verified locally using the Hub's configured Ed25519 public key.
