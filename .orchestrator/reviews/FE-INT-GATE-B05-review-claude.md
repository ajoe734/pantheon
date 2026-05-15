# Review: FE-INT-GATE-B05
Reviewer: Claude
Date: 2026-05-13
Task: F06 deepen — HIQ full flow and two-man required
Owner: Codex2
Artifact: execute-plans/e2e/05-interventions.spec.ts

## Verdict: APPROVED

All acceptance criteria are satisfied.

## Coverage Verified

1. **claim / release / escalate / decide** — Test 1 iterates all four actions against
   `/bff/v5/interventions/{id}/{action}`, asserts HTTP 202, and validates the full
   CommandResponse envelope (status, data.command, data.commandId, data.receipt.trackingUrl,
   meta.durable, meta.liveCapitalSideEffects).

2. **Idempotency-Key propagation** — each browser fetch sends
   `"b05-{action}-{uuid}"` as `Idempotency-Key`; harness captures it per-request;
   test asserts `idempotencyKey.startsWith("b05-")` for every request and
   `meta.idempotency.idempotencyKey` echoed in the CommandResponse.

3. **decide emits intervention.decided SSE** — Test 2 waits for SSE stream open
   (`expect.poll opens > 0`) before posting decide, then polls for
   `intervention.decided` event with correct `decidedBy`, `decision`, and
   `interventionId` in payload. SSE envelope shape and channel header are correct.

4. **Same-user two-man-sign → 409 TWO_MAN_REQUIRED** — Test 3 posts
   `secondOperatorId === OPERATOR_ID`, expects 409, validates error envelope:
   `code`, `i18nKey`, `message` regex, and `details` object
   (`entityType`, `entityId`, `kind`, `reason`).

## Harness Quality

- Pure `node:http` + Playwright, no extra test deps.
- Action route regex covers all five actions including `two-man-sign`.
- `actorFromAuthorization` correctly extracts actor from `Bearer <id>:…` format.
- SSE keep-alive tracking via `openSseResponses` array; cleanup on `req.close` and
  `harness.stop()` are correct and won't leak connections.
- `readJsonBody` safely handles empty body and validates parsed value is a
  non-array object.
- Both camelCase (`commandId`) and snake_case (`command_id`) fields present in
  CommandResponse matches the dual-encoding BFF contract.

## Verification (reported by Codex2)

In `/home/lupin/code/execute-plans`:
```
npx tsc --noEmit --pretty false       → 0 errors
npx playwright test e2e/05-interventions.spec.ts --list  → 3 tests
npx playwright test e2e/05-interventions.spec.ts          → 3 passed
```

## No Required Changes
