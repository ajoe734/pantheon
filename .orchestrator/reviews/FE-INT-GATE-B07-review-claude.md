# Review: FE-INT-GATE-B07 — F14 SSE Reconnect, Replay Cursor, and Resync

**Reviewer:** Claude  
**Owner:** Codex  
**Artifact:** execute-plans/e2e/08-sse-reconnect.spec.ts  
**Date:** 2026-05-13  
**Decision:** APPROVED

---

## Acceptance Criteria Verification

### AC1: 強制 close 後重連帶 Last-Event-Id ✅

Test 1 (`forced EventSource transport close reconnects with Last-Event-ID and receives heartbeat`) covers this:
- Browser SSE client receives `evt-first` from initial connection, stores it as `lastEventId`.
- `forceCloseEventSource()` calls `/__test__/drop-sse` on the harness, ending the server-side SSE response.
- Browser EventSource auto-reconnects per the EventSource specification with `Last-Event-Id: evt-first` header.
- Assertion: `harness.requests[1].lastEventIdHeader === "evt-first"` — passes.

### AC2: system.resync_required 觸發 refetch ✅

Test 2 (`system.resync_required refetches resync routes and reconnects without stale cursor`) covers this:
- `initialLastEventId: "evt-expired"` causes the SSE harness to immediately emit `system.resync_required` with `routes: ["/bff/approvals", "/bff/v5/interventions"]`.
- Browser client fetches both routes (GET, `Accept: application/json`) using `page.route` mocks.
- After resync, client clears `lastEventId = ""` and reconnects — confirmed by `harness.requests[last].lastEventIdQuery === null` and `lastEventIdHeader === undefined`.

### AC3: heartbeat 收到 ✅

Both tests poll for `system.heartbeat` in `state.events`. The SSE harness sends a heartbeat on the 2nd+ connection request, verified in both test paths.

---

## Technical Review

**SSE harness approach:** Correct — `page.route.fulfill()` cannot hold a streaming connection open for `EventSource`. Using a local `createServer` (Node.js `http.Server`) is the right approach.

**SSE block format:** Valid — `retry`, `id`, `event`, `data` fields followed by double newline (`\n\n`). Event parsing in browser client handles both `event.type` and `payload.type` for robustness.

**Browser client reconnect semantics:** Correct implementation:
- `state.lastEventId` tracks the latest event id.
- On `system.resync_required`: fetches all routes in parallel (via `for...of`), clears `lastEventId`, closes `EventSource` and calls `connect()` to create a fresh connection.
- `streamUrl()` only adds `last_event_id` query param if `state.lastEventId` is non-empty — ensures clean reconnect after resync.

**Type safety:** `SseRequestRecord`, `ResyncRequestRecord`, `OpenSseResponse` types are explicit. Playwright imports typed. esbuild compilation confirmed clean.

**Timeout values:** 5 000 ms `expect.poll` timeouts are appropriate for local server tests.

**Verification evidence (from handoff):**
- `npx playwright test execute-plans/e2e/08-sse-reconnect.spec.ts --reporter=line` → 2 passed
- `npx esbuild ... --bundle --platform=node --external:@playwright/test` → passed (no TypeScript errors)

---

## Notes

No issues found. All 3 acceptance criteria are fully covered by the two test cases. The SSE harness is a clean, self-contained implementation that correctly simulates both the reconnect-with-cursor and the resync-required code paths.
