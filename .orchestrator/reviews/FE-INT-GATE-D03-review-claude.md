# Review: FE-INT-GATE-D03 — F13 Agora signal ask journal

**Reviewer:** Claude  
**Date:** 2026-05-13  
**Artifact:** execute-plans/e2e/13-agora.spec.ts  
**Decision:** APPROVED

## Acceptance Criteria Check

| Criterion | Result | Evidence |
|---|---|---|
| signal feedback emits audit+SSE | ✅ PASS | Test 1 posts to `/bff/agora/signals/{id}/feedback`, verifies 202 + audit entry in response, readback via GET `/bff/audit?target_ref=signal:…`, and polls for `signal.feedback.recorded` + `operator.audit.updated` SSE events |
| ask REST 完整 transcript 可取 | ✅ PASS | Test 2 posts to `/bff/agora/ask`, polls for `ask.message.delta` × 2 + `ask.message.completed`, then GETs `/bff/agora/ask/sessions/{id}` and verifies transcript contains the reconstructed assistant message |
| journal patch 用 merge-patch+json 且 atomic | ✅ PASS | Test 3 sends PATCH with `Content-Type: application/merge-patch+json`; verifies 200 + version bump on valid patch; verifies 422 + `details.atomic=true` on invalid outcome; GET readback confirms no mutation from the rejected patch |
| SSE delta 不可用允許 skip | ✅ PASS | `const ASK_SSE_AVAILABLE = process.env.F13_AGORA_ASK_SSE_AVAILABLE !== "0"` — test 2 calls `test.skip(!ASK_SSE_AVAILABLE, …)` to allow graceful skip when set to "0" |

## Spec Quality

- **AgoraHarness** class is well-structured: ephemeral HTTP server on port 0, SSE channel tracking, proper teardown on `afterEach`
- Type aliases (`FeedbackResponse`, `AskSession`, `JournalEntry`, etc.) make assertions readable and self-documenting
- `installAgoraClient` correctly sets up EventSource for three channels and exposes `postJson`/`getJson`/`patchJournal` through `window.__pantheonF13`
- `waitForSseOpen` + `expect.poll` pattern correctly handles SSE async setup before posting actions
- Invalid patch test verifies both the error shape and the idempotency of state (readback still returns version 2, title unchanged)

## Verification (from owner)

- `npx playwright test e2e/13-agora.spec.ts --list` → 3 tests listed
- `npx playwright test e2e/13-agora.spec.ts` → 3/3 passed
- `npx esbuild e2e/13-agora.spec.ts --bundle --platform=node --format=esm …` → build clean

## Decision

All acceptance criteria satisfied. Spec is production-quality, well-typed, and follows harness patterns established by prior FE-INT-GATE specs. Approved for finalization.
