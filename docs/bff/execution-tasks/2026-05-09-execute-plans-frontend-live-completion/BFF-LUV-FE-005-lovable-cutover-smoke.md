# BFF-LUV-FE-005 - Lovable Live Cutover Smoke and Handoff

Priority: P0

Owner lane: cross-repo acceptance / release handoff

Repo:

- `/home/lupin/code/execute-plans`
- `/home/lupin/code/pantheon`

## Depends On

- `BFF-LUV-AUTHED-LIVE-001`
- `BFF-LUV-FE-001`
- `BFF-LUV-FE-002`
- `BFF-LUV-FE-003`
- `BFF-LUV-FE-004`

## Problem

Even after the code is wired, Lovable is not cut over until envs, build, route
smoke, authenticated DTO smoke, write smoke, and handoff evidence are published.

## Required Work

- Verify execute-plans branch is clean and pushed.
- Verify Lovable env target uses the intended BFF URL/mode.
- Run `npm run test` and `npm run build`.
- Run anonymous route smoke and authenticated DTO/write smoke evidence.
- Publish final handoff saying whether:
  - `VITE_BFF_MODE=live` is allowed;
  - `VITE_BFF_REAL_WRITES=true` is allowed;
  - remaining route families are blocked or complete.

## Acceptance Criteria

- All dependency tasks are done or explicitly blocked with owner/action.
- Evidence is published under `docs/bff/evidence/`.
- Handoff includes exact commit hashes for pantheon and execute-plans.
- Supervisor shows no active BFF-LUV frontend/live cutover tasks except explicit blockers.
