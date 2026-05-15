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

## Implementation Notes

- Added strict live read helpers for delivered Agora/v5 routes so live mode calls BFF and throws typed transport errors instead of silently returning seeded mock data.
- Added `bff.agora` for daily/signals/inbox/journal/ask read surfaces and routed Agora signal/inbox pages through it.
- Wired v5 control room, loop-runs, execution health, sentinel findings, and interventions through strict live adapters while keeping mock mode behavior intact.
- Added live SSE EventSource connection to `/bff/events/stream`, query replay via `lastEventId`, status updates from open/error, and legacy realtime data/v5 bridge events.
- Limited the mock realtime ticker and manual disconnect simulator to mock mode.

## Verification

- Execute-plans task commit: `8517b23b1102765955294ea5e681e0d541825d56`.
- `npm run test -- src/lib/bff/__tests__/liveAdapters.test.ts src/lib/v5/__tests__/bff.test.ts src/lib/bff-v1/__tests__/sse.test.ts` - pass, 14 tests.
- `npm run build` - pass; Vite emitted only existing chunk-size/dynamic-import warnings.
- `npm run test -- src/lib/bff/__tests__/liveAdapters.test.ts src/lib/bff/__tests__/client.test.ts` - pass, 21 tests.
- Closeout rerun `npm run test` - pass, 47 test files / 409 tests.
