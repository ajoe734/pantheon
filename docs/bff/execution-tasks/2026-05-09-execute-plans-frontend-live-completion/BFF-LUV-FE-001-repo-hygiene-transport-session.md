# BFF-LUV-FE-001 - Execute-Plans Repo Hygiene, Transport, and Session Foundation

Priority: P0

Owner lane: frontend integration / repo hygiene

Repo:

- `/home/lupin/code/execute-plans`

## Problem

`execute-plans` is behind `origin/main` and has uncommitted BFF wiring changes.
Before broad live wiring continues, the repo needs a clean integration branch,
published commits, and a trustworthy transport/session foundation.

## Write Scope

- `README.md`
- `.env.example`
- `.env.dev.example`
- `.env.staging-live.example`
- `package.json`
- `package-lock.json`
- `src/lib/bff/transport.ts`
- `src/lib/v4/session/me.ts`
- `src/platform/components/TopBar.tsx`
- `src/platform/components/RealtimeStatusBadge.tsx`

Avoid editing broad read adapters in `src/lib/bff/client.ts` unless required to
export the transport/session API.

## Required Work

- Reconcile current dirty changes against `origin/main` without losing user work.
- Create or keep a dedicated branch for execute-plans BFF live integration.
- Ensure BFF base URL/mode/write envs are documented for dev, lupin dev, and staging-live.
- Replace mock-only `/bff/me` session bootstrap with real/hybrid transport behavior.
- Wire refresh/logout if backend routes are available; otherwise document exact fallback.
- Ensure auth token storage/access is explicit and compatible with the current frontend shell.

## Acceptance Criteria

- `git status` in `execute-plans` is clean after commit.
- Branch is pushed to the appropriate remote.
- `npm run test` passes.
- `npm run build` passes.
- `/bff/me` is no longer silent mock in `real` mode.
- Hybrid fallback is documented route-by-route, not hidden.

## Delivery Notes

- Execute-plans branch: `feat/bff-luv-fe-001`
- Execute-plans commit: `ac104b446fe4d8f78df96ecf23a768ac8f754dd0`
- Pushed remote: `origin/feat/bff-luv-fe-001`
- Worktree state after push: clean; no remaining stash.

Implementation notes:

- Rebased the dirty work onto latest `origin/main`; `main` had moved from the old
  `src/lib/bff/client.ts` layout to the current `src/lib/bff-v1/*` transport.
- Kept the live/session foundation in the current `bff-v1` architecture instead
  of reintroducing obsolete `src/lib/bff/transport.ts`.
- `/bff/me` now uses live transport with explicit auto fallback and strict-mode
  failure surfacing; refresh/logout are wired through `/bff/auth/refresh` and
  `/bff/logout`.
- Auth access is explicit: cookie sessions use `credentials: "include"` and
  optional dev bearer/tenant keys are documented.
- Live writes are gated by `VITE_BFF_REAL_WRITES=false` by default.
- Route-by-route fallback is documented in execute-plans `README.md`.

Verification:

- `npm install --package-lock-only`
- `npm run test` -> 44 files / 369 tests passed.
- `npm run build` -> passed; Vite reported existing chunk-size and realtime
  dynamic/static import warnings only.

Owner closeout verification:

- 2026-05-09 Codex2 re-read reviewer approval and confirmed the approved
  execute-plans deliverable is still at
  `ac104b446fe4d8f78df96ecf23a768ac8f754dd0`.
- `git status --short --branch` in execute-plans -> clean on
  `feat/bff-luv-fe-001...origin/feat/bff-luv-fe-001`.
- `git rev-list --left-right --count @{u}...HEAD` in execute-plans -> `0 0`.
- `npm run test` -> 44 files / 369 tests passed.
- `npm run build` -> passed; same existing Browserslist, chunk-size, and
  realtime dynamic/static import warnings only.
