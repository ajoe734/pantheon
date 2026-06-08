# Execute-Plans Dev Frontend Hosting

Date: 2026-06-08

This is the canonical frontend hosting rule for Pantheon dev.

## Source Repository

- Active frontend repo: `ajoe734/execute-plans`
- Local checkout: `/home/lupin/code/execute-plans`
- Preferred work location for risky edits: a clean task worktree outside the
  dirty checkout, for example `/tmp/execute-plans-<task>`
- Delivery base as of 2026-06-08: `main`

Do not use `front-ai-trading-system` for new work. That repository name is
legacy-only. Do not recreate it, mirror new handoffs to it, or assign frontend
tasks to it.

## Dev Hosting Rule

Do not use Lovable as the Pantheon dev frontend host.

Lovable URLs can remain historical evidence or external reference points, but
they are not the dev deployment target and must not block Pantheon dev
acceptance. A stale Lovable bundle is not proof that the current
`execute-plans` commit failed.

The dev frontend should be served by Pantheon-owned infrastructure from the
recorded `execute-plans` commit. The intended host is:

- FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

If the FE hostname or VM IP changes, update this document and `AGENTS.md`
before routing work to the new target.

## Required Frontend Build Env

Build the dev frontend with live BFF wiring:

```sh
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
VITE_BFF_FALLBACK=strict
```

Keep write behavior safe by default:

```sh
VITE_BFF_REAL_WRITES=false
```

Only set `VITE_BFF_REAL_WRITES=true` when the operator explicitly asks for real
write-path testing and the corresponding BFF governance gates are ready.

## Required BFF CORS Env

Before browser smoke tests, the running dev BFF must allow the Pantheon-owned FE
origin:

```sh
PANTHEON_BFF_CORS_ORIGINS=...,https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
```

Do not rely on Lovable origins to validate the Pantheon dev FE. A local or
Pantheon-owned FE origin missing from `PANTHEON_BFF_CORS_ORIGINS` will fail in
the browser even when curl to the BFF succeeds.

## Deployment Shape

The dev deploy should:

1. Build `execute-plans` from the recorded commit.
2. Serve the build from the dev VM through Caddy or equivalent Pantheon-owned
   HTTPS routing.
3. Point the build at the dev BFF URL.
4. Add the FE origin to dev BFF CORS.
5. Restart only the services needed for the FE/CORS change.
6. Run browser smoke against the Pantheon-owned FE URL.

## Acceptance Smoke

Minimum smoke evidence:

- `GET /` on the FE host returns the new build.
- The loaded JS bundle contains the intended dev BFF URL and does not contain
  obsolete BFF URLs.
- `/bff/me` from the browser returns the expected authenticated or governed
  auth response.
- Management AI routes can read BFF provider/control-mode status.
- SSE opens from the browser origin or has an explicit tested fallback.
- If write paths are tested, the result is either a governed success or a
  documented fail-closed response.

## CI Rule

Pull-request CI may run Playwright against a local PR frontend so stale external
hosting cannot block a valid branch. Post-merge and dev deployment smoke should
target the Pantheon-owned FE host above, not Lovable.

Any workflow or script that still defaults to `https://pantheon-dev.lovable.app`
for dev acceptance should be treated as legacy until it is updated to accept the
Pantheon-owned FE URL.

## Legacy Automation Warning

Some historical orchestration scripts and tests still mention
`front-ai-trading-system`, `lovable-ui-task`, or Lovable publish flows. Do not
use those paths to route current frontend work until they have been explicitly
migrated to `execute-plans` and Pantheon-owned dev hosting.

Known legacy surfaces include:

- `.orchestrator/coordination_repo_mirror.py`
- `.orchestrator/lovable_task_publisher.py`
- `scripts/bootstrap_front_repo.sh`
- `scripts/coordination_publish_handoff.py`
- `scripts/coordination_drift_guard.py`

If a worker needs to use one of these flows, update the code/config first and
put that change through PR review before dispatching frontend work.
