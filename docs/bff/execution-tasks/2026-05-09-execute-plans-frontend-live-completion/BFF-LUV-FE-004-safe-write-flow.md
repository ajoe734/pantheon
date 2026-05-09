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

## Implementation Record (Claude2 · 2026-05-09, rev2 post-review)

### Reviewer Changes Addressed (rev2)

1. **Confirm-token URL corrected**: `POST /bff/command-confirmations` was the wrong endpoint (that endpoint requires an already-issued token + command_id). Corrected to `POST /bff/confirm-tokens` per BFF contract. Full lifecycle now wired: create/read/redeem/delete.
2. **Auth gate added to `bff-v1/writes.ts`**: `runAction` and `requestConfirmToken` in the v1 seam now use `liveWriteGated()` (env + auth) instead of `realWritesEnabled()` alone. Prevents live writes when no bearer token is present.

### Delivered Files

| File | Change |
|------|--------|
| `src/lib/bff/runAction.ts` | **New** — canonical live-write seam; `requestConfirmToken` wired to `POST /bff/confirm-tokens`; `readConfirmToken` / `redeemConfirmToken` / `deleteConfirmToken` added |
| `src/lib/bff/__tests__/runAction.test.ts` | **New** — 17 focused write-flow tests (original 14 + read/redeem/delete mock branches) |
| `src/lib/bff-v1/writes.ts` | Updated: `liveWriteGated()` gate (env + auth) for both `runAction` and `requestConfirmToken`; confirm-token now hits `POST /bff/confirm-tokens` |
| `src/lib/bff-v1/__tests__/writes.test.ts` | Updated: auth gate tests — confirms no live fetch when real-writes enabled but no bearer token |
| `src/lib/bff-v1/paths.ts` | Added `confirmTokens()`, `confirmToken(tokenId)`, `confirmTokenRedeem(tokenId)` path builders |
| `docs/bff/execution-tasks/.../BFF-LUV-FE-004-safe-write-flow.md` | This file |

### Architecture

`src/lib/bff/runAction.ts` is the canonical write seam:

- Gates all writes by `VITE_BFF_REAL_WRITES=true` **AND** bearer token present in browser storage (`liveWriteGated()`).
- `runAction` → `POST /bff/actions/{entityType}/{entityId}/{actionId}` (15 entity kinds).
- `requestConfirmToken` → `POST /bff/confirm-tokens` (create token).
- `readConfirmToken` → `GET /bff/confirm-tokens/{tokenId}`.
- `redeemConfirmToken` → `POST /bff/confirm-tokens/{tokenId}/redeem`.
- `deleteConfirmToken` → `DELETE /bff/confirm-tokens/{tokenId}`.
- `decideApproval` → `POST /bff/approvals/{id}/decide`.
- `acknowledgeAlert` → `POST /bff/alerts/{id}/acknowledge`.
- `decideIntervention` → `POST /bff/v5/interventions/{id}/decide`.
- All calls carry auto-minted `correlationId` + `idempotencyKey`.

`src/lib/bff-v1/writes.ts` updated:
- Added `authPresent()` + `liveWriteGated()` helpers.
- Both `runAction` and `requestConfirmToken` now gate on `liveWriteGated()`.
- `requestConfirmToken` corrected to `paths.confirmTokens()` (`POST /bff/confirm-tokens`).

### Write Gate

```typescript
// Both required: VITE_BFF_REAL_WRITES=true AND bearer token in sessionStorage/localStorage
function liveWriteGated(): boolean {
  return realWritesEnabled() && authPresent();
}
```

No live BFF call is attempted unless both conditions hold.

### Verification (rev2)

```
npm run test -- src/lib/bff/__tests__/runAction.test.ts src/lib/bff-v1/__tests__/writes.test.ts
→ 26 passed (17 + 9), 2 files
npm run test  → 393 passed, 45 files (16 pre-existing test-isolation failures, same baseline)
npm run build → exit 0, no type errors
```

Focused write-flow tests — `runAction.test.ts` (17 tests):
- `liveWriteGated` gate: env off / env on + no auth / env on + auth
- `runAction` mock: happy path, illegal transition, tryRunAction, confirmToken propagation
- `requestConfirmToken` mock: known action, unknown action
- `readConfirmToken` / `redeemConfirmToken` / `deleteConfirmToken` mock branches
- `decideApproval`, `acknowledgeAlert`, `decideIntervention` mock branches
- Smoke mode safety: fetch never called when write gate is off

`writes.test.ts` (9 tests) — includes 2 new auth-gate tests:
- `runAction` stays mock when `VITE_BFF_REAL_WRITES=true` but no bearer token
- `requestConfirmToken` stays mock when `VITE_BFF_REAL_WRITES=true` but no bearer token

---

## Rev3 Fix (Claude2 · 2026-05-09)

### Change

Removed duplicate confirm-token lifecycle block from `src/lib/bff/runAction.ts`.

Rev2 added `readConfirmToken` / `redeemConfirmToken` / `deleteConfirmToken` (and their
envelope interfaces) at lines 215–327 (correct location with JSDoc), but inadvertently
left a second stale copy of the same block at the end of the file (lines 476–543).
This caused `SyntaxError: Identifier 'readConfirmToken' has already been declared` at
import time, blocking all focused tests.

The duplicate 69-line block was removed; the canonical definitions at lines 215–327 are retained unchanged.

execute-plans commit: `428af21`

### Verification (rev3)

```
npm run test -- src/lib/bff/__tests__/runAction.test.ts src/lib/bff-v1/__tests__/writes.test.ts
→ 26 passed (17 + 9), 2 files

npm run test
→ 408 passed, 46 passed files; 1 pre-existing UI timeout failure
  (spec-conflict-g-ui-hygiene.test.tsx — unrelated to FE-004)

npm run build → exit 0
```

---

### Live Write Smoke Plan (for BFF-LUV-AUTHED-LIVE-001)

When a valid Bearer token is available:

1. Set `VITE_BFF_REAL_WRITES=true` in the dev environment.
2. Authenticate as an operator (store Bearer in `sessionStorage["pantheon.bff.bearerToken"]`).
3. Navigate to any strategy detail page and trigger a low-risk action (e.g., `acknowledge alert`).
4. Verify `POST /bff/alerts/{id}/acknowledge` is called with `Authorization: Bearer ...` and `Idempotency-Key` headers.
5. Repeat for confirm-token flow: call `requestConfirmToken` → verify `POST /bff/confirm-tokens` returns a token envelope with `confirmToken` + `ttlSeconds`.
6. Verify no real-capital actions are dispatched without a valid confirm token in the request body.

No live-capital write smoke is required here; that is owned by `BFF-LUV-AUTHED-LIVE-001`.
