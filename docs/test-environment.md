# Disposable test environment

Status: canonical how-to for local acceptance testing. Owner: repository
maintainers. Review whenever `deploy/docker-compose.test.yml`, migrations, or
environment settings change.

This environment runs PostgreSQL, Redis, the migration job, Hub API, background
worker, dashboard, edge agent, and Mailpit. It binds only the Hub (`127.0.0.1:8080`),
dashboard (`127.0.0.1:3000`), Mailpit UI (`127.0.0.1:8025`), and test SMTP
(`127.0.0.1:1025`) to localhost. It is disposable and must never receive real
customer data or production secrets.

## Prerequisites

- Git and a clean checkout of this repository.
- Docker Desktop or Docker Engine with Compose v2 and Linux containers.
- At least 4 GB of free memory and 10 GB of free disk.
- Python 3.11 or 3.12. Go and Node are optional unless running source-level checks.
- On Linux, permission to access `/var/run/docker.sock`. That socket is effectively
  root-equivalent; use only this disposable host or a dedicated VM.

## 1. Prepare the repository

PowerShell:

```powershell
cd C:\tools\remotemonitortool\control-plane
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r hub\requirements.txt
& .\.venv\Scripts\python.exe scripts\bootstrap_test_config.py
```

Bash:

```bash
cd /path/to/control-plane
python3 -m venv .venv
.venv/bin/python -m pip install -r hub/requirements.txt
.venv/bin/python scripts/bootstrap_test_config.py
```

The bootstrap command creates four ignored local artifacts:

- `deploy/test.env`: random database, owner, agent, TOTP, Vault, and public-key values.
- `deploy/test-license.txt`: a 30-day enterprise license signed by the generated key.
- `deploy/test-totp-uri.txt`: an `otpauth://` URI for a test authenticator entry.
- `deploy/test-fixtures/compose/`: harmless compose and `.env` Vault fixtures.

Do not commit, paste into tickets, or reuse any of these values. Re-running the
bootstrap invalidates the current stack's credentials; reset its volumes at the
same time if you intentionally rotate the test environment.

## 2. Start the complete topology

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml up -d --build
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml ps
```

Use forward slashes on Bash. The `migrate` container should exit with code 0;
PostgreSQL, Redis, Hub, worker, dashboard, agent, and Mailpit should remain up.
If the Hub is not healthy after two minutes:

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml logs --tail 200 migrate hub worker agent
```

## 3. Run automated acceptance

PowerShell:

```powershell
& .\.venv\Scripts\python.exe scripts\test_environment_smoke.py
```

Bash:

```bash
.venv/bin/python scripts/test_environment_smoke.py
```

The smoke run applies the generated enterprise license and verifies:

1. PostgreSQL and Redis readiness.
2. Authentication fails closed without an owner token.
3. Offline license verification and immediate white-label refresh.
4. Agent connection and telemetry.
5. Redis-backed durable ping dispatch and command completion.
6. Agent-side Vault encryption and Hub ciphertext metadata ingestion.
7. TOTP-gated emergency diagnostics and grant revocation.
8. Scoped viewer read access plus mutation denial.
9. Audit events and owner-gated Prometheus metrics.

A second run is safe. The ping uses an idempotency key; temporary viewer tokens
are revoked before the script exits.

## 4. Use the dashboard

Open [http://localhost:3000](http://localhost:3000). On Settings, paste the
`ADMIN_TOKEN` value from `deploy/test.env` into **Operator token**. The generated
license is already active if the smoke test ran. The node page should show
`test-agent-01`, telemetry, Vault history, remote actions, and break-glass
diagnostics.

Import the URI in `deploy/test-totp-uri.txt` into a test authenticator if you want
to unlock diagnostics manually. Never import this test secret into a production
authenticator collection.

## 5. Manual feature exercises

### Notification delivery and alert lifecycle

Create an SMTP channel in Settings with:

```json
{
  "host": "mailpit",
  "port": 1025,
  "from": "alerts@test.local",
  "to": ["operator@test.local"],
  "starttls": false
}
```

Use **Test** and inspect [http://localhost:8025](http://localhost:8025). To test
OFFLINE and RESOLVED delivery, stop the agent for at least 45 seconds, then start
it again:

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml stop agent
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml start agent
```

### Tenant-safe enrollment token

The base stack initially uses the default-tenant legacy bootstrap credential so
it can start unattended. In Settings create an agent token for group `default`
and save the one-time `cpa_...` value. Replace `AGENT_BOOTSTRAP_TOKEN` in
`deploy/test.env` with that value and recreate only the agent:

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml up -d --no-deps --force-recreate agent
```

The running Hub still knows the previous legacy value, but the recreated agent
authenticates through the stored one-time enrollment-token record. Revoking that
record and recreating the agent should make authentication fail closed.

### Docker update staging

Set `IMAGE_UPDATE_CHECK_ENABLED=true` for the agent and allow a digest refresh.
When the dashboard reports an update, **Pull image** downloads it through the
local Docker Engine but does not restart the workload. For a private registry,
mount a dedicated, read-only Docker config and set `DOCKER_CONFIG_FILE`; never put
registry credentials in Hub settings or a command payload.

### Proxmox integration

This requires a real disposable Proxmox node or lab cluster. Configure the agent
with `PROXMOX_API_URL`, `PROXMOX_TOKEN_ID`, `PROXMOX_TOKEN_SECRET`,
`PROXMOX_CA_FILE`, and `PROXMOX_GUEST_ALLOWLIST`. Use a privilege-separated API
token with audit and power-management rights only for the selected guests. Mount
the cluster CA rather than disabling TLS verification. Start with a disposable
stopped guest, then verify start, graceful shutdown, reboot, command history, and
audit records.

### Two-owner destructive approval

The default disposable environment disables two-owner approval so one operator
can finish acceptance. To test it, set `REMOTE_COMMAND_TWO_PERSON_APPROVAL=true`,
create two distinct owner API tokens, recreate the Hub, request a Proxmox reboot
with owner A, and approve it with owner B. Approval by owner A must return 409.

## 6. Source-level release checks

Run these after any fix made during testing:

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///./data/manual-check.db'
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m alembic check
& .\.venv\Scripts\python.exe -m unittest discover -s hub\tests -v
& .\.tools\go\bin\go.exe test ./...
& .\.tools\go\bin\go.exe vet ./...
& .\.venv\Scripts\python.exe scripts\build_agent_artifacts.py --output-dir dist\agents
Push-Location frontend; npm run build; Pop-Location
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml config --quiet
docker compose --env-file deploy\pi.env.example -f deploy\docker-compose.pi.yml config --quiet
```

Use the system `go` executable when the workspace-local toolchain is absent.

## 7. Stop, resume, or erase

Stop without deleting data:

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml stop
```

Resume with `start`. Remove containers and networks while retaining PostgreSQL:

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml down
```

The following command permanently deletes only the Compose project's named test
volume. Verify the project name is exactly `control-plane-test` before running it:

```powershell
docker compose --env-file deploy\test.env -p control-plane-test -f deploy\docker-compose.test.yml down --volumes
```

After volume removal, delete the four ignored generated artifacts or rerun the
bootstrap to create a fresh isolated identity.

## Troubleshooting

- **Dashboard API calls target localhost from inside its container:** rebuild the
  dashboard. `HUB_URL` is intentionally supplied as a frontend build argument.
- **Agent cannot connect:** inspect agent and Hub logs, confirm `AUTH_TOKEN`
  matches the active legacy or stored enrollment credential, and preserve the
  `ws://hub:8080/api/v1/agent/stream` container-network URL.
- **Vault returns 402:** apply `deploy/test-license.txt` or rerun the smoke test.
- **Vault reports no files:** confirm `deploy/test-fixtures/compose` exists and is
  mounted at `/host/compose`.
- **Emergency console is hidden:** confirm it is enabled and the dashboard has
  the owner token. TOTP clocks must be within
  roughly 30 seconds.
- **Docker socket permission denied:** run in a dedicated Linux VM or grant the
  test agent access deliberately. Do not broaden production permissions just to
  make this lab pass.
- **Windows bind-mount problems:** Docker Desktop must be using Linux containers
  and have access to the repository drive.
