# BFF-LUV-FE-003 - Agora, v5, and Live Realtime Wiring

Priority: P0

Owner lane: frontend realtime / v5 integration

Repo:

- `/home/lupin/code/execute-plans`

## Problem

Agora surfaces, v5 loop/sentinel surfaces, and realtime are still largely
derived from seeded mock state. The BFF now exposes the route families and SSE
compatibility routes, so execute-plans needs live adapters.

## Write Scope

- `src/lib/bff/v5.ts`
- `src/lib/bff/realtime.ts`
- `src/lib/useLiveList.ts`
- new files under `src/lib/bff/` for Agora/realtime adapters
- focused tests for v5/Agora/realtime behavior

Avoid editing broad Management Console adapters owned by `BFF-LUV-FE-002`.

## Required Work

- Wire Agora read surfaces to BFF where routes exist.
- Wire v5 loop-runs, sentinel findings, interventions, and persona/strategy health.
- Replace mock-only realtime status with EventSource-based BFF SSE support.
- Preserve replay/heartbeat semantics and keep mock simulator available only in mock mode.

## Acceptance Criteria

- Agora and v5 pages can run in `real` mode without seeded mock fallback for delivered routes.
- EventSource connection reaches live BFF SSE routes when auth is available.
- Reconnect/replay behavior is tested or explicitly blocked by BFF response.
- `npm run test` passes.
- `npm run build` passes.
