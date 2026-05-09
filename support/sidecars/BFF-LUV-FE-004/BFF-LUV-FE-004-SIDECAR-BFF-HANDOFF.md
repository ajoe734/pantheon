# BFF-LUV-FE-004 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-FE-004-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-FE-004
Helper kind: bff_handoff_packet
Owner: Codex
Reviewer: Claude2
Prepared: 2026-05-09T17:42:31Z

## Scope

Support-only sidecar for BFF-LUV-FE-004. This packet does not define canonical
architecture, change route truth, or modify runtime/frontend implementation. It
organizes the safe-write BFF gap, operator journey, and frontend handoff notes
for the parent owner to absorb or ignore.

Current parent state at packet time:

- Parent owner: Claude2.
- Parent reviewer: Codex.
- Parent status: `in_progress`.
- Existing review packet: `.orchestrator/reviews/BFF-LUV-FE-004-review-codex.md`
  records `changes requested`.
- Blocking review theme: live success payloads are not normalized into the
  frontend seam's declared `CommandResponse` / confirm-token shapes.

## Source Snapshot

| Surface | Current state | Source |
|---|---|---|
| Write gate | Requires `VITE_BFF_REAL_WRITES=true` and a browser Bearer token before live writes are attempted. | `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:33`; `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:42` |
| Canonical action write | Uses `POST /bff/actions/{entityType}/{entityId}/{actionId}` when the write gate is open. | `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:126` |
| Confirm-token lifecycle | Uses `/bff/confirm-tokens`, `/bff/confirm-tokens/{tokenId}`, and `/bff/confirm-tokens/{tokenId}/redeem`. | `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:196`; `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:238`; `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:275` |
| Decision writes | Approval, alert acknowledge, and v5 intervention decision are wired to BFF paths. | `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:362`; `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:407`; `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts:457` |
| Live transport | `withLiveOrMock` returns raw live JSON unless the caller supplies `adaptLive`. | `/home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts:47` |
| Backend command receipt | Current BFF command routes return `status`, `data`, and `meta`, not a top-level frontend `ok: true` envelope. | `services/control-plane/bff/main.py:21819` |

## BFF Query Gap Matrix

The route registration is present; the remaining gap is response normalization
between backend command receipts and the frontend write seam.

| Frontend call | BFF route | Backend success shape observed | Frontend seam shape expected | Handoff note |
|---|---|---|---|---|
| `runAction(input)` | `POST /bff/actions/{entityType}/{entityId}/{actionId}` | `status: "accepted"`, `data.commandId`, `data.receipt`, `meta.idempotency` | `ok: true`, `data.actionId`, `data.status`, `correlationId`, `idempotencyKey` | Add an explicit `adaptLive` callback; do not return the backend receipt raw. Preserve command id as `jobId`, `auditEventId`, or receipt metadata according to the parent seam decision. |
| `requestConfirmToken(req)` | `POST /bff/confirm-tokens` | Command receipt plus `data.tokenId`, `data.id`, `data.status: "created"` | `ConfirmTokenResponse` with `confirmToken`, `ttlSeconds`, `requiredPhrase`, `requiresMemo`, `auditEventPreview` | Normalize `confirmToken` from `tokenId`. The BFF does not currently return phrase/TTL fields, so the parent owner must either derive display-only fields from `highRiskActions.ts` or keep this as a blocker for a backend shape extension. |
| `readConfirmToken(tokenId)` | `GET /bff/confirm-tokens/{tokenId}` | `{ data: { id, tokenId, status }, meta: ... }` | Same `ConfirmTokenResponse` type currently used by the frontend seam | Current seam type is probably too rich for the read route. If retained, adapt `confirmToken` from `tokenId` and make missing TTL/phrase behavior explicit in tests. |
| `redeemConfirmToken(tokenId)` | `POST /bff/confirm-tokens/{tokenId}/redeem` | Command receipt | `{ tokenId, redeemed: true }` inside `CommandResponse` | The current adapter fabricates the minimal data shape; add a live test that asserts top-level `ok`, `correlationId`, and `idempotencyKey` survive. |
| `deleteConfirmToken(tokenId)` | `DELETE /bff/confirm-tokens/{tokenId}` | Command receipt | `{ tokenId, deleted: true }` inside `CommandResponse` | Same as redeem; include idempotency replay coverage. |
| `decideApproval(id, decision, memo)` | `POST /bff/approvals/{id}/decide` | Generic accepted alias: `status`, `data.id`, `data.status`, `meta.snapshot_at` | `{ approvalId, decision }` inside `CommandResponse` | Current adapter falls back to caller values because backend response has no `approvalId`; that is acceptable only if the returned command/alias status is still tested. |
| `acknowledgeAlert(id, memo)` | `POST /bff/alerts/{id}/acknowledge` | Generic accepted alias: `status`, `data.id`, `data.status`, `meta.snapshot_at` | `{ alertId }` inside `CommandResponse` | Current adapter already returns minimal caller-derived data; add live-mode fetch tests. |
| `decideIntervention(id, decision, memo)` | `POST /bff/v5/interventions/{id}/decide` | Command receipt | `{ interventionId, decision }` inside `CommandResponse` | Same command-receipt adapter pattern as approval. |

## Operator Journey

This is the recommended operator smoke path after the parent fix lands and a
valid lupin-dev Bearer token is available.

1. Session bootstrap: set `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
   keep `VITE_BFF_REAL_WRITES=false`, store a valid Bearer token in
   `sessionStorage["pantheon.bff.bearerToken"]`, and verify `/bff/me` returns a
   valid identity before any write smoke.
2. Gate-negative smoke: with `VITE_BFF_REAL_WRITES=false`, call `runAction` and
   `requestConfirmToken`; assert `fetch` is not called and mock overlay behavior
   remains available.
3. Auth-negative smoke: set `VITE_BFF_REAL_WRITES=true` but remove the Bearer
   token; assert `fetch` is still not called.
4. Non-capital confirm-token smoke: set `VITE_BFF_REAL_WRITES=true` with auth,
   call create -> read -> delete for `/bff/confirm-tokens`, and verify the UI
   receives the normalized frontend envelope, not raw `status/data/meta`.
5. Non-capital command smoke: prefer `acknowledgeAlert` or v5 intervention
   decision over deployment/capital/strategy live actions. Verify
   `Idempotency-Key` and `X-Correlation-Id` are present on mutation requests
   and the normalized result carries frontend `correlationId` and
   `idempotencyKey`.
6. Replay smoke: repeat the same idempotency key with the same body and verify
   a replayed command result on routes backed by the command idempotency store
   such as `/bff/actions/*` or `/bff/confirm-tokens`. Reuse the same
   idempotency key with a different body and verify a typed 409 conflict.

Do not run smoke against live-capital side-effect routes:

- strategy deploy/promote/pause/resume/rollback/emergency-kill actions;
- deployment create/patch;
- capital allocation or rebalance mutations;
- any route that can emit a broker order or change real capital exposure.

## Frontend Handoff Notes

- Treat `/home/lupin/code/execute-plans/src/lib/bff/runAction.ts` as the primary
  FE-004 write seam. The older `src/lib/bff-v1/writes.ts` still exposes
  compatibility helpers but has the same raw-live-response risk for action and
  confirm-token writes.
- Every live write helper should use `withLiveOrMock(req, mockBranch, adaptLive)`.
  Omitting `adaptLive` is unsafe for command routes because the backend receipt
  shape is not the frontend `CommandResponse` shape.
- Focus tests should force live mode with auth, mock `fetch`, return representative
  backend JSON, and assert the caller receives the normalized frontend envelope.
  Mock/smoke-mode tests must remain in place to prove no live fetch occurs when
  either the env gate or auth gate is closed.
- UI callers should surface 401/403/409/428 as real backend errors. These should
  not fall back to mock because `withLiveOrMock` intentionally propagates typed
  4xx BFF errors.
- Confirm-token UI copy should not rely on the current backend read route to
  provide `ttlSeconds` or `requiredPhrase`; those fields are absent from the
  current BFF response. Use local high-risk action metadata only if the parent
  owner accepts that as frontend-derived display data.

## Parent Absorption Checklist

Before BFF-LUV-FE-004 returns for review, the parent owner should confirm:

- `runAction` has a live adapter for the backend command receipt.
- `requestConfirmToken` and `readConfirmToken` no longer cast raw backend JSON
  to `ConfirmTokenResponse`.
- `redeemConfirmToken`, `deleteConfirmToken`, `decideApproval`,
  `acknowledgeAlert`, and `decideIntervention` have live tests that assert
  normalized success envelopes.
- Safety gates still require both `VITE_BFF_REAL_WRITES=true` and browser auth.
- Smoke plan remains limited to non-capital routes until authenticated live
  DTO/write evidence exists under the AUTHED-LIVE task.

## Verification Notes For This Sidecar

No runtime or frontend implementation was changed by this sidecar. Verification
for the packet consisted of source inspection only:

```bash
jq '.tasks[] | select(.id=="BFF-LUV-FE-004-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,260p' docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-004-safe-write-flow.md
sed -n '1,560p' /home/lupin/code/execute-plans/src/lib/bff/runAction.ts
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/writes.ts
sed -n '21810,22280p' services/control-plane/bff/main.py
git diff --check -- support/sidecars/BFF-LUV-FE-004/BFF-LUV-FE-004-SIDECAR-BFF-HANDOFF.md
git status --short -- support/sidecars/BFF-LUV-FE-004/BFF-LUV-FE-004-SIDECAR-BFF-HANDOFF.md
```

## Reviewer Handoff

Reviewer (Claude2) should verify:

1. This packet is support-only and does not modify canonical truth, runtime
   implementation, registry state, or frontend implementation.
2. The gap matrix matches current FE-004 review findings: route wiring and
   safety gates are present, but live success normalization is still the
   critical handoff item.
3. The operator journey excludes live-capital side-effect smoke.
4. Parent owner can use this packet as advisory input without treating it as
   an approved replacement for the BFF-LUV-FE-004 implementation record.

This packet is ready for Claude2 review and parent-owner absorption decision.
