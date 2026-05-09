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

---

## Implementation Record (Claude2 · 2026-05-09)

### Delivered Files

| File | Change |
|------|--------|
| `src/lib/bff/runAction.ts` | **New** — canonical live-write seam |
| `src/lib/bff/__tests__/runAction.test.ts` | **New** — 14 focused write-flow tests |
| `src/lib/bff-v1/writes.ts` | Updated: canonical `paths.action()` for all kinds; confirm-token live endpoint |
| `src/lib/bff-v1/paths.ts` | Added `commandConfirmations()` and `commandConfirmation(token)` path builders |
| `docs/bff/execution-tasks/.../BFF-LUV-FE-004-safe-write-flow.md` | This file |

### Architecture

`src/lib/bff/runAction.ts` is the new canonical write seam that:

- Gates all writes by `VITE_BFF_REAL_WRITES=true` **AND** a bearer token present in browser storage.
- Uses `paths.action(entityType, entityId, actionId)` — canonical `/bff/actions/{entityType}/{entityId}/{actionId}` for all entity kinds (15 entity types mapped).
- Exports `requestConfirmToken` → live `POST /bff/command-confirmations` with mock fallback.
- Exports `decideApproval` → live `POST /bff/approvals/{id}/decide` with mock fallback.
- Exports `acknowledgeAlert` → live `POST /bff/alerts/{id}/acknowledge` with mock fallback.
- Exports `decideIntervention` → live `POST /bff/v5/interventions/{id}/decide` with mock fallback.
- All calls carry auto-minted `correlationId` + `idempotencyKey`; both can be overridden by the caller.

`src/lib/bff-v1/writes.ts` updated:
- `actionPath()` now uses `paths.action()` (canonical) for all entity kinds instead of deprecated per-entity path builders.
- `requestConfirmToken` now hits `POST /bff/command-confirmations` when `realWritesEnabled()`, falling back to mock.

### Write Gate

```typescript
// VITE_BFF_REAL_WRITES=true AND bearer token present in sessionStorage/localStorage
export function liveWriteGated(): boolean {
  return realWritesEnabled() && authPresent();
}
```

No live BFF call is ever attempted unless both conditions hold. In smoke/mock mode all mutations go through the in-memory mock layer.

### Verification

```
npm run test  → 383 passed, 45 test files (all green)
npm run build → ✓ built in 56.24s (no errors)
```

Focused write-flow test file: `src/lib/bff/__tests__/runAction.test.ts` — 14 tests covering:
- `liveWriteGated` gate logic (env × auth combinations)
- `runAction` mock branch (happy path, illegal transition, tryRunAction, confirmToken propagation)
- `requestConfirmToken` mock branch (known action, unknown action)
- `decideApproval`, `acknowledgeAlert`, `decideIntervention` mock branches
- Smoke mode safety (fetch never called when write gate is off)

### Live Write Smoke Plan (for BFF-LUV-AUTHED-LIVE-001)

When a valid Bearer token is available:

1. Set `VITE_BFF_REAL_WRITES=true` in the dev environment.
2. Authenticate as an operator (store Bearer in `sessionStorage["pantheon.bff.bearerToken"]`).
3. Navigate to any strategy detail page and trigger a low-risk action (e.g., `acknowledge alert`).
4. Verify `POST /bff/alerts/{id}/acknowledge` is called with `Authorization: Bearer ...` and `Idempotency-Key` headers.
5. Repeat for confirm-token flow: request a confirm token → verify `POST /bff/command-confirmations` returns token envelope.
6. Verify no real-capital actions are dispatched without a valid confirm token in the request body.

No live-capital write smoke is required here; that is owned by `BFF-LUV-AUTHED-LIVE-001`.
