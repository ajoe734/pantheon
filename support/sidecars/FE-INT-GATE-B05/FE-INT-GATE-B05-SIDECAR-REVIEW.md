# FE-INT-GATE-B05 — Sidecar Review Packet

**Packet type:** review_packet (sidecar support artifact)
**Sidecar task:** FE-INT-GATE-B05-SIDECAR-REVIEW
**Parent task:** FE-INT-GATE-B05
**Prepared by:** Claude (sidecar worker)
**Reviewer:** Codex
**Date:** 2026-05-13
**Parent task status at packet creation:** review_approved

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-B05 |
| Title | F06 deepen — HIQ full flow and two-man required |
| Owner | Codex2 |
| Reviewer | Claude |
| Phase | Pantheon FE Integration Gate 2026-05-13 |
| Final status | review_approved |

**Scope (summary_zh):** F06 HIQ 升級：claim / release / escalate / decide 四個 action 端到端跑；/decide 回 CommandResponse 並產 intervention.decided SSE；同 user two-man sign 回 TWO_MAN_REQUIRED。

---

## 2. Artifact Under Review

**Primary artifact:** `execute-plans/e2e/05-interventions.spec.ts`

This file contains a Playwright E2E test suite (`F06 HIQ interventions`) covering three tests:

1. `runs claim, release, escalate, and decide through CommandResponse routes`
2. `decide returns CommandResponse and publishes intervention.decided SSE`
3. `same-user two-man sign returns TWO_MAN_REQUIRED`

---

## 3. Acceptance Criteria Assessment

| Criterion | Status | Evidence |
|---|---|---|
| claim / release / escalate / decide 4 個 action 走通 | **PASS** | Test 1 iterates all four actions against `/bff/v5/interventions/{id}/{action}`, asserts HTTP 202, and validates the full CommandResponse envelope (status, data.command, data.commandId, data.receipt.trackingUrl, meta.durable, meta.liveCapitalSideEffects) |
| decide 回 CommandResponse 且觸發 intervention.decided SSE | **PASS** | Test 2 polls for SSE stream open (`state.opens > 0`) before posting decide; then polls for `intervention.decided` event with correct `decidedBy`, `decision`, and `interventionId`; SSE envelope shape and channel header are correct |
| same-user two-man sign 回 TWO_MAN_REQUIRED | **PASS** | Test 3 posts `secondOperatorId === OPERATOR_ID`, expects HTTP 409, validates full error envelope: `code`, `i18nKey`, `message` regex, and `details` object (`entityType`, `entityId`, `kind`, `reason`) |

**Overall verdict: APPROVED**

---

## 4. Technical Evidence Detail

### 4.1 Harness Architecture

`InterventionHarness` uses a plain `node:http` server (no extra deps). Route dispatch:

1. `GET /` or `/test-shell` → minimal HTML shell for Playwright browser context
2. `GET /bff/events/stream?channel=intervention` → SSE stream with correct headers (`Content-Type: text/event-stream`, `X-SSE-Channel: intervention`, `X-SSE-Replay-Supported: true`)
3. `POST /bff/v5/interventions/{id}/(claim|release|escalate|decide|two-man-sign)` → `handleAction`
4. Anything else → 404 `RESOURCE_NOT_FOUND`

Route regex: `/^\/bff\/v5\/interventions\/([^/]+)\/(claim|release|escalate|decide|two-man-sign)$/`

### 4.2 CommandResponse Envelope

`commandResponse(interventionId, action, idempotencyKey)` produces the dual-encoding BFF contract:

```typescript
{
  status: "accepted",
  data: {
    action,                          // echoed action
    command: "V5InterventionAction",
    commandId: "cmd-b05-{action}-N",
    command_id: "cmd-b05-{action}-N",  // dual snake_case
    receipt: { command_id, status: "accepted", trackingUrl },
    receipt_id: commandId,
    target: { id: interventionId, type: "SentinelIntervention" },
  },
  meta: {
    durable: true,
    idempotency: { idempotencyKey, replayed: false },
    liveCapitalSideEffects: false,
  },
}
```

`commandResponseAt()` helper validates all critical fields with labelled assertions.

### 4.3 Idempotency-Key Propagation

Browser client sends `"b05-{action}-{crypto.randomUUID()}"` as `Idempotency-Key` for every POST. Harness captures it per-request. Test 1 asserts:
- `harness.requests.every(r => r.idempotencyKey?.startsWith("b05-"))` — all 4 requests carry a B05-prefixed key
- `response.meta.idempotency.idempotencyKey` echoed in CommandResponse

### 4.4 SSE Lifecycle and decide → intervention.decided Flow

`handleSse` writes `": connected\n\n"` keep-alive on open and tracks `openSseResponses[]`. Cleanup is correct: `req.on("close")` splices the response from the array; `harness.stop()` drains remaining open connections before server close.

After the browser client opens the SSE channel, Test 2:

1. Polls `state.opens > 0` to confirm channel is live before POSTing decide
2. `handleAction` calls `publishDecision()` which writes a `intervention.decided` SSE block to all open responses
3. Test polls `sseEvents(page)` for the matching event and asserts `decidedBy`, `decision`, `interventionId`

### 4.5 two-man-sign Same-Actor Rejection

`actorFromAuthorization` parses `Bearer <actorId>:roles:mfa` → `actorId`. If `secondOperatorId === actorId` (or empty), responds HTTP 409:

```typescript
{
  detail: {
    error: {
      code: "TWO_MAN_REQUIRED",
      i18nKey: "errors.TWO_MAN_REQUIRED",
      message: "Two-man authorization requires a distinct second operator",
      retryable: false,
      userActionable: true,
      details: {
        actionId: "V5InterventionAction",
        entityType: "SentinelIntervention",
        entityId: interventionId,
        kind: "two_man",
        reason: "TWO_MAN_DISTINCT_OPERATOR_REQUIRED",
      },
    },
  },
}
```

Test 3 asserts every field of this envelope.

### 4.6 Browser Client Architecture

`installBrowserClient` injects `window.__pantheonB05` into the page context, providing:
- `state.opens` — SSE open count
- `state.events[]` — received SSE events
- `postAction(action, body)` — sends the fetch with correct headers including `Idempotency-Key: b05-{action}-{uuid}`
- `close()` — closes the EventSource

`page.evaluate` round-trips through Playwright's serialization; `BrowserActionResult` captures both `status` and `body`.

### 4.7 Async Correctness

Test 2 polls for `state.opens > 0` **before** posting decide. This prevents a race where the decide POST fires before the SSE channel acknowledges the browser connection.

### 4.8 Execution Evidence (as reported by Codex2 and Claude review)

```
npx tsc --noEmit --pretty false       → 0 errors
npx playwright test e2e/05-interventions.spec.ts --list  → 3 tests
npx playwright test e2e/05-interventions.spec.ts          → 3 passed
```

---

## 5. Minor Observations

| Item | Severity | Assessment |
|---|---|---|
| `occurredAt` is hardcoded to `"2026-05-13T13:30:00Z"` in `publishDecision` | Info | Acceptable — deterministic timestamp for contract harness; matches sprint date |
| `readJsonBody` validates parsed body is non-null, non-array object | Info | Correct defensive check; would throw `expect` assertion failure on malformed body — appropriate for test harness |
| `sseEvents` helper fetches only `type` and `payload` for assertion | Info | Sufficient for the test scope; raw `id`/`channel` fields not needed for acceptance criteria |
| two-man-sign also rejects on empty `secondOperatorId` | Info | Extra guard beyond acceptance criteria; harmless and reasonable |

---

## 6. Review Decision

**APPROVED** — all three acceptance criteria satisfied. The implementation is correct on:
- HIQ full flow: all four actions return HTTP 202 CommandResponse with correct envelope
- Idempotency-Key round-trip: every request carries and echoes the key
- SSE flow: decide triggers `intervention.decided` event with correct payload
- Two-man gate: same-user sign correctly returns HTTP 409 `TWO_MAN_REQUIRED`

No required changes. Returning to Codex2 (owner) for finalization.

---

## 7. Handoff Note

This packet is prepared for **Codex review**. The review evidence is recorded in:
- `.orchestrator/reviews/FE-INT-GATE-B05-review-claude.md` — Claude's original review file
- `ai-status.json` — parent task `FE-INT-GATE-B05` status: `review_approved`

**Next action for Codex2 (parent task owner):**
Run the closeout checklist per `.orchestrator/skills/task-closeout-finalization.md`:
1. Re-read this packet and the review file
2. Verify `execute-plans/e2e/05-interventions.spec.ts` still matches approved state
3. Create task-scoped commit (subject includes `FE-INT-GATE-B05`)
4. Run `AI_NAME=Codex2 ./scripts/ai-status.sh done FE-INT-GATE-B05 "<checkpoint message>"`
5. Push to configured upstream

**Sidecar task (FE-INT-GATE-B05-SIDECAR-REVIEW):** Prepared by Claude (owner). Handed off to Codex for review.
