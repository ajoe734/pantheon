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

## Verification Record (Claude — 2026-05-10)

### Dependency Tasks

All 5 dependency tasks confirmed `done`:
- BFF-LUV-AUTHED-LIVE-001 ✓
- BFF-LUV-FE-001 ✓
- BFF-LUV-FE-002 ✓
- BFF-LUV-FE-003 ✓
- BFF-LUV-FE-004 ✓

### Lovable Env / BFF URL

- `.env` already contains `VITE_BFF_MODE=live` and `VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

### Smoke Results

| Smoke | Evidence File | Result |
|-------|--------------|--------|
| Anonymous route probe | BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json | 63 passed, 338 OpenAPI paths |
| Authenticated DTO + write | BFF-LUV-AUTHED-LIVE-001-live-smoke-20260510T024935Z.json | 37/37 passed (30 read, 5 write) |

### Execute-Plans Build/Test

```
npm run test -- --run   → 47 test files, 418 tests passed (108s)
npm run build           → ✓ built in 1m 1s, 2835 modules
```

### Commit Hashes

- `execute-plans`: `b276e50` on `feat/bff-luv-fe-001` (pushed)
- `pantheon`: `3a0a2d01` on `backend-dev-publish-20260429`

### Final Lovable Handoff Decision

| Setting | Decision |
|---------|----------|
| `VITE_BFF_MODE=live` | **ALLOWED** — smoke 37/37 pass |
| `VITE_BFF_REAL_WRITES=true` | **ALLOWED** — write smoke passed; `liveCapitalSideEffects=false` confirmed |
| Production capital-side-effect paths | **BLOCKED** — fail-closed per `PAPER_CANARY_LIVE_POLICY`; not lifted by this task |
| Route family gaps | **None** — all authenticated + write families pass |

Evidence file: `docs/bff/evidence/BFF-LUV-FE-005-cutover-smoke-20260510T030200Z.json`
