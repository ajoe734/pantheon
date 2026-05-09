# Review: BFF-LUV-FE-004

Reviewer: Codex
Date: 2026-05-09
Decision: **approved**

## Scope Reviewed

Task: Wire execute-plans safe real write flows
Owner: Claude2
execute-plans commit reviewed: `3f33e60`
Pantheon artifact commit reviewed: `9a46b17b`

Artifacts reviewed:
- `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-004-safe-write-flow.md`
- `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts`
- `/home/lupin/code/execute-plans/src/lib/bff/__tests__/runAction.test.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/writes.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/__tests__/writes.test.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/runActionSafe.ts`
- `/home/lupin/code/execute-plans/src/platform/components/HighRiskConfirm.tsx`
- `/home/lupin/code/execute-plans/src/management/pages/StrategyDetail.tsx`
- `/home/lupin/code/execute-plans/src/management/pages/RebalanceDetail.tsx`
- `/home/lupin/code/execute-plans/src/management/pages/CapitalPoolDetail.tsx`
- `/home/lupin/code/execute-plans/src/management/pages/RankingFormulaDetail.tsx`
- `/home/lupin/code/execute-plans/src/management/pages/PersonaDetail.tsx`
- `services/control-plane/bff/main.py`

## Rev6 Final Assessment

Approved. Rev6 fixes the remaining blocking issue from the prior review:
the `confirmToken` issued by `HighRiskConfirm` now reaches
`runActionSafe(..., { confirmToken })` at all five v3 high-risk detail-page
call sites reviewed here: Strategy, Rebalance, CapitalPool, RankingFormula,
and Persona.

The UI-facing v1 write seam already normalizes live receipts for `runAction`
and `requestConfirmToken`; Rev6 adds a focused live test proving
`opts.confirmToken` is serialized into the live `/bff/actions/*` POST body.
The memo-string token embedding workaround was removed from Strategy and
Rebalance, so the token is now passed through the typed write path instead of
being hidden in audit memo text.

## Verification Run

```bash
npm run test -- src/lib/bff-v1/__tests__/writes.test.ts src/lib/bff/__tests__/runAction.test.ts
# Passed: 34 tests, 2 files

npm run test
# Passed: 418 tests, 47 files

npm run build
# Passed; existing dynamic-import and chunk-size warnings only.
```

## Acceptance Assessment

Approved for owner closeout. Acceptance is met for env + auth gated live
writes, idempotency propagation, confirm-token/action envelope wiring, DTO
normalization coverage, and smoke-mode safety.

---

## Historical Rev4 Assessment

Rev4 fixes the prior blocking issue for the newer
`src/lib/bff/runAction.ts` seam:

- `runAction` now adapts `_sem_command_response` into a top-level
  `RunActionEnvelope`.
- `requestConfirmToken` maps backend `data.tokenId` to frontend
  `data.confirmToken` and derives TTL/phrase fields from the local high-risk
  action catalog.
- `readConfirmToken` maps backend `data.tokenId` / `data.id` to
  `data.confirmToken`.
- Focused live-mode tests were added for the new seam's action and
  confirm-token lifecycle paths.

That is real progress, but the task still cannot be approved because the
UI-facing v1 write seam remains unnormalized in live mode.

## Blocking Finding

1. `src/lib/bff-v1/writes.ts` still returns raw live BFF receipts for the
   UI-facing write helpers.

   The application imports `runActionSafe`, `runAction`, and
   `requestConfirmToken` from `@/lib/bff-v1` across management detail pages and
   `HighRiskConfirm`. In that path, `runAction` calls
   `withLiveOrMock<RunActionEnvelope>` without an `adaptLive` callback at
   `src/lib/bff-v1/writes.ts:115`, and `requestConfirmToken` does the same at
   `src/lib/bff-v1/writes.ts:173`.

   Because `withLiveOrMock` returns raw live JSON when no adapter is supplied,
   a successful live response from `/bff/actions/*` or `/bff/confirm-tokens`
   still has backend `status/data/meta` shape rather than the promised frontend
   `CommandResponse` shape. This leaves these UI paths broken in live mode:

   - `runActionSafe` returns `r.envelope.legacy` at
     `src/lib/bff-v1/runActionSafe.ts:40`; the raw backend receipt has no
     `legacy`.
   - `HighRiskConfirm` reads `r.data.confirmToken`,
     `r.data.requiredPhrase`, and `r.data.expiresAt` at
     `src/platform/components/HighRiskConfirm.tsx:136`; the raw backend create
     response has `data.tokenId` / `data.id` / `data.status` instead.

   The new `src/lib/bff/runAction.ts` tests do not cover these public v1
   imports, and `src/lib/bff-v1/__tests__/writes.test.ts` currently tests mock
   branches and auth-negative gating only. So the acceptance item
   "confirm-token/action/decision envelopes wired" is still not true for the
   actual UI write surface.

## Required Fix

- Add live `adaptLive` callbacks to `src/lib/bff-v1/writes.ts` for
  `runAction` and `requestConfirmToken`, matching the normalized behavior now
  present in `src/lib/bff/runAction.ts`.
- Add focused v1 live-mode tests that force live transport with auth, mock
  `fetch`, and assert:
  - `runAction` returns top-level `ok`, `data.actionId`, `auditEventId`,
    `correlationId`, `idempotencyKey`, and a usable `legacy` object for
    `runActionSafe`.
  - `requestConfirmToken` maps backend `data.tokenId` to
    `data.confirmToken` and provides the fields consumed by
    `HighRiskConfirm`.
- Keep the existing gate tests proving no live fetch happens unless
  `VITE_BFF_REAL_WRITES=true` and browser auth is present.

## Historical Verification Run

```bash
npm run test -- src/lib/bff/__tests__/runAction.test.ts src/lib/bff-v1/__tests__/writes.test.ts
# Passed: 31 tests, 2 files

npm run build
# Passed; existing dynamic import and chunk-size warnings only.
```

## Historical Acceptance Assessment

Not approved yet at Rev4. Rev4 closed the newer seam's normalization bug, but
the frontend's public v1 write surface still returned raw backend live receipts
for the same action and confirm-token flows. This historical finding was
resolved before the Rev6 approval above.
