# BFF-LUV-FE-006 - Dev Deploy and Frontend BFF E2E Closure

Priority: P0

Owner lane: release closure / dev deployment / end-to-end acceptance

Repos:

- `/home/lupin/code/execute-plans`
- `/home/lupin/code/pantheon`

## Depends On

- `BFF-LUV-AUTHED-LIVE-001`
- `BFF-LUV-FE-001`
- `BFF-LUV-FE-002`
- `BFF-LUV-FE-003`
- `BFF-LUV-FE-004`
- `BFF-LUV-FE-005`

## Goal

After all execute-plans BFF live wiring work is complete, perform the final
release closure:

- collect and reconcile all code, task artifacts, evidence, and handoffs;
- ensure all new development is committed and pushed in the relevant repos;
- deploy the new frontend/BFF integration to the dev environment;
- test that the deployed frontend actually uses Pantheon BFF successfully.

## Preflight

Do not deploy until all dependency tasks are `done` or have an explicit approved
blocker disposition. If any dependency is blocked, this task must publish a
single closure blocker that names the exact missing owner/action.

## Required Work

- Verify `pantheon` and `execute-plans` worktrees are clean.
- Verify all relevant local branches are pushed and record exact commit hashes.
- Confirm frontend env for dev:
  - `VITE_BFF_MODE`
  - `VITE_BFF_BASE_URL`
  - `VITE_BFF_REAL_WRITES`
  - auth/session configuration
- Deploy the latest execute-plans frontend integration to the dev environment.
- Confirm the dev BFF target is healthy:
  - `/health`
  - `/openapi.json`
  - representative protected route auth behavior.
- Run deployed frontend smoke that proves the UI is using BFF:
  - session/bootstrap path;
  - Management Console read page;
  - v5/Agora page;
  - realtime/SSE connection or explicit tested fallback;
  - safe write smoke if auth/write gates allow it.
- Capture browser/network evidence or automated smoke output showing BFF calls
  from the deployed frontend.
- Publish final evidence under `docs/bff/evidence/`.

## Acceptance Criteria

- `pantheon` branch is clean, committed, and pushed.
- `execute-plans` branch is clean, committed, and pushed.
- Dev deploy completed from the recorded execute-plans commit.
- Deployed frontend smoke proves BFF requests are issued to the intended dev BFF
  URL and return expected `2xx`/governed auth outcomes.
- Final evidence includes:
  - deployment target URL;
  - Pantheon commit hash;
  - execute-plans commit hash;
  - BFF target URL;
  - smoke command(s);
  - route/status summary;
  - pass/fail decision for `VITE_BFF_MODE=live`;
  - pass/fail decision for `VITE_BFF_REAL_WRITES=true`.
- Supervisor shows no active BFF-LUV frontend/live tasks except explicitly
  approved blockers.
