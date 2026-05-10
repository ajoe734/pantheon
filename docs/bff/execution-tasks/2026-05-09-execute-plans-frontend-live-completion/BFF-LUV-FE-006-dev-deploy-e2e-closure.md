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

---

## Closure — Accepted 2026-05-10

Reviewer: Codex  
Owner finalization: Claude  
Review approved at: 2026-05-10T06:02:03Z  
Finalization commit: see below

### Final State

| Item | Value |
|---|---|
| Pantheon branch | `backend-dev-publish-20260429` |
| Pantheon HEAD | `ab7a4044` (BFF-LUV-FE-006: record browser CORS verification) |
| execute-plans HEAD | `e25f5c7` / `198522c` on `origin/main` |
| Deployed URL | `https://pantheon-dev.lovable.app/` |
| Deployed bundle | `b944ef3a` / `index-hGWC2E4H.js` |
| BFF target | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` |
| VITE_BFF_MODE | `live` ✓ |
| VITE_BFF_REAL_WRITES | `false` (defaults safe; `true` allowed per policy) |

### Acceptance Criteria Outcome

| Criterion | Outcome |
|---|---|
| pantheon branch committed and pushed | Met — `ab7a4044` pushed |
| execute-plans branch committed and pushed | Met — `e25f5c7` on `origin/main` |
| dev deploy from recorded commit | Met — `b944ef3a` serves `198522c` bundle |
| deployed frontend proves BFF requests reach dev BFF | Met — post-CORS-fix browser probe: 5 requests, 5 responses, 0 failed |
| final evidence published with live/write decisions | Met — `docs/bff/evidence/` committed at `ab7a4044` |
| supervisor no active BFF-LUV tasks | Met on `done` transition |

### Key Evidence Files

- `docs/bff/evidence/BFF-LUV-FE-006-e2e-closure-20260510T031500Z.json` — E2E closure: 418 tests pass, 37/37 authenticated smoke
- `docs/bff/evidence/BFF-LUV-FE-006-pantheon-dev-lovable-publish-20260510.md` — hosted bundle probe + CORS fix + post-fix browser network probe
- `docs/bff/evidence/BFF-LUV-FE-006-lovable-deploy-blocker-20260510.md` — Lovable deploy blocker (resolved: obsolete URL probed)
- `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-live-smoke-20260510T024935Z.json` — 37/37 authenticated live smoke

### CORS Fix Committed During Review

Codex ran a real Chromium browser probe against `https://pantheon-dev.lovable.app` and
discovered the running dev BFF allowed only the legacy origin. Fix:

- Commit `45bf6873` — added `https://pantheon-dev.lovable.app` to `PANTHEON_BFF_CORS_ORIGINS`
- Dev BFF on `pantheon-lupin-dev` restarted at `45bf6873`
- Post-fix probe (2026-05-10T06:00:59Z): 5/5 BFF requests completed, CORS `200 OK`, `401` on protected routes (expected auth gates)

### Write Gate Decision

- `VITE_BFF_MODE=live` — allowed and active ✓
- `VITE_BFF_REAL_WRITES=true` — allowed per operator handoff decision; defaults `false` in hosted env for safety
- Live capital side effects: remain fail-closed per `PAPER_CANARY_LIVE_POLICY` ✓
