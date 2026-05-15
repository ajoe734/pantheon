---
task_id: FE-INT-GATE-C04
reviewer: Claude
verdict: approve
reviewed_at: 2026-05-14
artifact: execute-plans/e2e/16-audit-correlation.spec.ts
---

# Review: FE-INT-GATE-C04 — F16 Audit and Correlation Chain

## Verdict: APPROVE

All four acceptance criteria are satisfied. The spec is clean and the harness is correctly wired.

## Acceptance Criteria Check

| Criterion | Result |
|---|---|
| X-Request-Id sent in request and echoed in response | ✅ Test 1 asserts both `request.requestId` server-side and `responseHeaders.requestId` client-side |
| X-Correlation-Id consistent across request / response / audit log / audit SSE | ✅ Test 1 checks all four legs: request header captured in harness, response header, audit log item, and SSE payload via `expect.poll` |
| Durable audit event and SSE event share `correlationId` | ✅ Both `auditItems[0].correlationId` and the SSE `audit.event` payload `correlationId` are asserted against the same `expectedCorrelationId` |
| Mock overlay renders ephemeral badge only in mock mode | ✅ Test 2 posts live (no badge), then mock (badge visible with correct text + correlation-id attribute), then polls for TTL expiry within 3 s |

## Code Quality

- **Harness design**: `AuditCorrelationHarness` class is well-contained; `start()`/`stop()` lifecycle is clean. SSE connections are tracked and closed on teardown.
- **Deterministic IDs**: All IDs use `seeded*` helpers — no random drift between runs.
- **SSE correctness**: `installSseController` is called before the POST, and `waitForSseOpen` gates the test so the SSE connection is live before the action is sent. No ordering hazard.
- **`auditEntryFromResponse`**: Handles both `auditEvent` and `audit_event` keys per the BFF dual-field convention.
- **Relative-URL fetch in browser context**: `getAuditLog` uses a relative path; the page is served by the harness, so it resolves correctly.
- **TTL timeout**: Badge TTL is 600 ms; `expect.poll` timeout is 3 000 ms — ample margin, not flaky.
- **`normalizeSourceMode` fallback**: Anything other than `"mock"` becomes `"live"`. Correct default.

## Minor Notes (non-blocking)

- The body includes a `correlationId` field alongside the header. The harness reads from the header, so the body field is unused — no issue, just belt-and-suspenders.
- `SNAPSHOT_AT` is re-declared locally and differs from `fixtures.ts` (`15:10` vs `14:30`). Both are test timestamps; the spec does not import from fixtures so there is no conflict.

## Summary

Implementation is complete and correct. Commit `db38bc1a` delivers all required coverage. Ready for owner finalization.
