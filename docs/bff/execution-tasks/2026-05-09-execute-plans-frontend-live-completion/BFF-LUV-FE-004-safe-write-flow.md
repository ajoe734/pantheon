# BFF-LUV-FE-004 - Safe Real Write Flow Wiring

Priority: P0

Owner lane: governed frontend writes

Repo:

- `/home/lupin/code/execute-plans`

## Problem

The frontend still keeps real writes disabled. High-risk confirmation, action
dispatch, approval/intervention decisions, alert acknowledgement, and command
receipt handling need to use Pantheon BFF safely before `VITE_BFF_REAL_WRITES`
can be enabled.

## Write Scope

- `src/lib/bff/mutations.ts`
- `src/lib/bff/runAction.ts`
- `src/lib/v3/highRiskActions.ts`
- `src/lib/bff/writeOverlay.ts`
- focused write-flow tests

Avoid read adapter files owned by `BFF-LUV-FE-002` and realtime files owned by
`BFF-LUV-FE-003`.

## Required Work

- Wire confirm-token create/read/redeem/delete if available.
- Wire canonical action dispatch through `/bff/actions/{entityType}/{entityId}/{actionId}`.
- Wire approval/intervention decisions to BFF command/receipt envelopes.
- Keep writes blocked unless `VITE_BFF_REAL_WRITES=true` and auth is present.
- Ensure no live-capital side effects are possible in smoke mode.

## Acceptance Criteria

- Real write calls are guarded by env + auth + idempotency key.
- DTO/receipt normalization is tested.
- Mock overlay remains available in mock/hybrid fallback only.
- `npm run test` passes.
- `npm run build` passes.
- A live write smoke plan is ready for `BFF-LUV-AUTHED-LIVE-001`.
