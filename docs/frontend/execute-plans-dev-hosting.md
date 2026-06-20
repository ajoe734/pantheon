# Execute-Plans Dev Frontend Hosting

Date: 2026-06-11

This is the canonical frontend hosting rule for Pantheon dev.

This document supersedes the dev-hosting portions of these legacy Lovable
runbooks:

- `docs/deployment/lovable-dev-staging-operating-rules.md`
- `docs/deployment/frontend-lovable-environments.md`
- `docs/deployment/bff-https-ingress.md`
- `docs/deployment/nonprod-development-workflow.md`

Those files may still be useful as staging-live or historical Lovable context,
but they are not the dev frontend hosting source of truth.

## Source Repository

- Active frontend repo: `ajoe734/execute-plans`
- Local checkout: `/home/lupin/code/execute-plans`
- Preferred work location for risky edits: a clean task worktree outside the
  dirty checkout, for example `/tmp/execute-plans-<task>`
- Delivery base as of 2026-06-11: `dev`

Do not use `front-ai-trading-system` for new work. That repository name is
legacy-only. Do not recreate it, mirror new handoffs to it, or assign frontend
tasks to it.

## Dev Hosting Rule

Do not use Lovable as the Pantheon dev frontend host.

Lovable URLs can remain historical evidence or external reference points, but
they are not the dev deployment target and must not block Pantheon dev
acceptance. A stale Lovable bundle is not proof that the current
`execute-plans` commit failed.

Do not ask the operator to press Lovable publish for Pantheon dev delivery, and
do not block on Lovable connector authorization. Current dev deployment flows
through GitHub PRs, an `execute-plans` build, and Pantheon-owned HTTPS hosting.

The dev frontend should be served by Pantheon-owned infrastructure from the
recorded `execute-plans` commit. The intended host is:

- FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

If the FE hostname or VM IP changes, update this document and `AGENTS.md`
before routing work to the new target.

## Current Verified Dev Deployment

Verified on 2026-06-11:

- Backend/BFF repo: `ajoe734/pantheon`
- Backend/BFF branch: `dev`
- Backend/BFF merge commit:
  `0d9fe5864a9b39b1775dcc94da91a54357cdeb9d`
- Backend/BFF deploy evidence:
  GitHub Actions run `27357842338`, `Pantheon Nonprod Deploy`, deployed
  `0d9fe5864a9b39b1775dcc94da91a54357cdeb9d`; the `Nonprod deploy` job
  completed in `10m17s` with `Deploy requested VM stack` and `Public BFF
  smoke` successful.
- Frontend repo: `ajoe734/execute-plans`
- Frontend branch: `dev`
- Frontend merge commit:
  `721bc3c4fe22648c242c6e39c353939575a33637`
- Frontend dev VM document root: `/var/www/pantheon-dev-fe/`
- Frontend deployment manifest:
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
  reports `app=execute-plans`, `sourceBranch=dev`,
  `sourceRef=721bc3c4fe22648c242c6e39c353939575a33637`,
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and
  `VITE_BFF_REAL_WRITES=false`.

If an agent sees a different Lovable bundle, that is not the Pantheon dev FE.
Validate the Pantheon-owned host and the GitHub commits above before changing
code.

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

## Agora Compatibility Gate

Agora dev deployment is gated by the generated cross-repo manifest:

```text
docs/contracts/agora/dev-compatibility-manifest.json
```

For repo sanity checks, the Pantheon side may verify a pending manifest while
the execute-plans generated type mirror is still catching up:

```sh
python3 scripts/agora_compat_manifest.py verify \
  --allow-pending \
  --manifest docs/contracts/agora/dev-compatibility-manifest.json
```

For an actual dev deployment, pending status is not enough. The deployment gate
must pass against the immutable backend commit and, when available, the matching
execute-plans manifest from the frontend repo:

```sh
python3 scripts/agora_compat_manifest.py deployment-gate \
  --manifest docs/contracts/agora/dev-compatibility-manifest.json \
  --frontend-manifest /home/lupin/code/execute-plans/docs/contracts/agora/dev-compatibility-manifest.json \
  --backend-runtime-commit <pantheon-backend-commit>
```

The gate fails closed when either repo has placeholder commits, mismatched
bundle/OpenAPI hashes, stale generated types, missing required Agora
capabilities, or `compatibility_status != compatible`.

## Deployment Shape

The dev deploy should:

1. Build `execute-plans` from the recorded commit.
2. Run the Agora compatibility deployment gate for Agora frontend/BFF changes.
3. Serve the build from the dev VM through Caddy or equivalent Pantheon-owned
   HTTPS routing.
4. Point the build at the dev BFF URL.
5. Add the FE origin to dev BFF CORS.
6. Restart only the services needed for the FE/CORS change.
7. Run browser smoke against the Pantheon-owned FE URL.

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

Management AI/OpenClaw dev work has an additional readiness gate. Provider
readiness is not enough: `/bff/assistant/mode` must report
`kernel_enabled: true`, and control mode must be activatable by an authorized
operator/admin session before claiming that Management AI can read/write VM
files or coordinate debugging through OpenClaw. See
`docs/operations/management-ai-openclaw-dev-bridge.md`.

Do not use `/bff/assistant/tools/*` as the VM file access proof. That route
family is for governed Pantheon action preview/validation/execute contracts.
OpenClaw VM inspection goes through Management AI conversation routes such as
`POST /bff/management/nl/ask`; write-capable repair also requires valid
`openclaw.repair` metadata and a clean repair task worktree under the configured
repair root.

Operator POST routes in the final BFF contract require a stable
`Idempotency-Key` header. `X-Idempotency-Key` is accepted only as a temporary
compatibility alias. Do not place idempotency keys in the JSON body.

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
- `docs/delivery-coordination-bus.md` before its 2026-06-08 supersession note

If a worker needs to use one of these flows, update the code/config first and
put that change through PR review before dispatching frontend work.
