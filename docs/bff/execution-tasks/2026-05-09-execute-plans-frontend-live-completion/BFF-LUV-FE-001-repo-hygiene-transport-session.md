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
