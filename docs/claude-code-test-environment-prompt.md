# Claude Code handoff: build and validate the Control Plane test environment

Copy the prompt below into Claude Code from the repository root.

---

You are working in the `control-plane` repository. Build, start, and validate the
disposable local test environment described in `docs/test-environment.md`.

Rules:

1. Read `README.md`, `docs/test-environment.md`, `deploy/docker-compose.test.yml`,
   and the smoke script before taking action.
2. Inspect `git status` first. Preserve all pre-existing user changes. Do not
   reset, discard, or overwrite unrelated work.
3. Use the fixed Compose project name `control-plane-test`. Do not touch any
   other Docker project, container, network, image, or volume.
4. Treat `deploy/test.env`, `deploy/test-license.txt`,
   `deploy/test-totp-uri.txt`, and `deploy/test-fixtures` as secrets/ignored
   runtime artifacts. Never commit or paste their contents into output.
5. Do not use real customer data, production credentials, production endpoints,
   or a production Proxmox cluster.
6. Do not weaken TLS, authentication, RBAC, allowlists, TOTP, result-size limits,
   or two-person approval to make a test pass.
7. Do not add arbitrary shell execution. Emergency diagnostics must remain the
   fixed allowlist implemented in `pkg/diagnostics`.
8. If a test reveals an implementation defect, diagnose it with evidence, make
   the smallest scoped fix, add or update a regression test, and rerun the
   affected component plus the full release checks. Do not redesign unrelated
   features.
9. Do not commit unless explicitly asked. Leave a concise list of changed files
   and exact verification results.

Execution:

1. Confirm Docker Compose v2 and Python 3.11/3.12 are available.
2. Create or reuse `.venv`, then install `hub/requirements.txt`.
3. Run `scripts/bootstrap_test_config.py`. Report generated file paths only, not
   secret values.
4. Render `deploy/docker-compose.test.yml` with `deploy/test.env` and fail on any
   interpolation or schema error.
5. Build and start the stack with project name `control-plane-test`.
6. Wait for PostgreSQL, Redis, and Hub health. Confirm the migration job exited 0
   and the Hub, worker, dashboard, agent, and Mailpit are running.
7. Run `scripts/test_environment_smoke.py` and preserve its PASS/FAIL summary.
8. Open or HTTP-check the dashboard on port 3000 and Mailpit on port 8025. Confirm
   the dashboard's same-origin `/api` proxy reaches the Hub.
9. Exercise an SMTP channel against `mailpit:1025`; confirm a test message appears.
10. Stop the agent long enough to observe OFFLINE/FIRING, restart it, and confirm
    ONLINE/RESOLVED plus notification behavior.
11. Test a stored one-time agent enrollment token and revocation as documented.
12. Run the source-level checks: Alembic upgrade/check, all Hub tests, Python
    compile and dependency check, Go test/vet/build, frontend production build,
    and Compose config validation.
13. Run an SQLite backup and isolated restore verification using the existing DR
    scripts. If a disposable PostgreSQL drill database is available, also test a
    dump/restore using a database name ending in `_restore_drill`.
14. Do not attempt Proxmox lifecycle actions unless a disposable lab endpoint,
    restricted token, CA file, and explicitly allowlisted throwaway VMID are
    provided. Otherwise mark live Proxmox testing as an external dependency, not
    a failure.
15. Do not run Docker image pulls unless the image is already used by a running
    disposable test container and its registry is allowlisted. Confirm no
    workload is automatically recreated or restarted.

Acceptance criteria:

- The automated smoke script passes every check.
- No unauthenticated tenant endpoint succeeds.
- The agent appears ONLINE and telemetry updates.
- A durable ping reaches SUCCEEDED through Redis and the outbound tunnel.
- Vault produces a COMPLETE ciphertext-only backup with at least two fixture files.
- A TOTP grant runs an allowlisted diagnostic, expires/revokes correctly, and the
  event appears in audit history.
- A scoped viewer can read its group and cannot mutate.
- SMTP test delivery reaches Mailpit; an alert resolves after agent recovery.
- Migrations have no model drift; all unit tests and production builds pass.
- `git diff --check` passes, generated secrets remain ignored, and no unexpected
  tracked files or Docker resources are modified.

Final report format:

- Overall result: PASS, PARTIAL, or FAIL.
- Environment inventory and container health.
- Automated smoke results.
- Manual scenario results, one line per scenario.
- Source-level verification with exact test counts.
- Defects found and fixes made, with file paths.
- External tests not run and the exact missing dependency.
- Cleanup state: left running, stopped, or removed. Do not delete volumes unless
  explicitly instructed.

---
