# FE-INT-GATE-C04 Review Packet

Sidecar Kind: review_packet
Parent Task: FE-INT-GATE-C04 — F16 new — Audit and correlation chain
Sidecar Task: FE-INT-GATE-C04-SIDECAR-REVIEW
Prepared by: Claude
Reviewer: Codex2
Prepared at: 2026-05-14T02:00:00Z

---

## 1. Parent Task Summary

**FE-INT-GATE-C04** implements the F16 Audit / Correlation spec for the Pantheon FE Integration Gate.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | X-Request-Id sent by browser and echoed in response | ✅ |
| 2 | X-Correlation-Id consistent across request, response, audit log, and audit SSE | ✅ |
| 3 | Durable audit event and SSE `audit.event` share `correlationId` | ✅ |
| 4 | Mock overlay audit badge renders only in mock mode and expires (ephemeral) | ✅ |

---

## 2. Delivered Artifact

**File:** `execute-plans/e2e/16-audit-correlation.spec.ts`  
**Delivery commit:** `db38bc1a3fce523abe120e6bd2896f289fb3ad16`  
**Runner mirror:** `/home/lupin/code/execute-plans/e2e/16-audit-correlation.spec.ts`  

The Pantheon artifact and sibling runner mirror were verified byte-identical at finalization.

---

## 3. Test Coverage

Two Playwright browser tests delivered:

### Test 1: `echoes request ids and keeps correlation ids aligned through audit and SSE`

Validates the full correlation chain:
- Browser sends `X-Request-Id` and `X-Correlation-Id` headers in POST to `/bff/strategies/{id}/audit-actions`
- Response echoes both headers back in `X-Request-Id` and `X-Correlation-Id`
- Server-side `RequestRecord` captures both IDs from incoming headers
- Durable `AuditEntry` stores `requestId` and `correlationId` from the same headers
- `GET /bff/audit?correlation_id=...` returns the matching entry with identical IDs
- SSE channel (`/bff/events/stream?channel=audit`) publishes `audit.event` event containing matching `correlationId` and `requestId`
- Live mode: no overlay badge is rendered

### Test 2: `renders ephemeral overlay audit badge only for mock mode`

Validates mock-mode isolation:
- Live POST: `mockMode: false`, no `overlayBadge`, no badge element rendered in DOM
- Mock POST (`X-Pantheon-Source-Mode: mock`): `mockMode: true`, `overlayBadge` present with `kind: "mock_overlay_audit"`, `ephemeral: true`, `ttlMs: 600`
- Badge appears in DOM with correct `data-testid`, text including `auditId`, and `data-correlation-id` attribute
- Badge auto-removes within 3 s TTL via `expect.poll`

---

## 4. Harness Architecture

`AuditCorrelationHarness` class:
- Spins up a local HTTP server on a random port in `beforeEach`
- Handles: HTML shell (`/test-shell`), SSE stream (`/bff/events/stream?channel=audit`), audit POST, and audit GET log
- Tracks open SSE responses for broadcast and clean teardown
- ID generation uses `seeded*` helpers from `helpers/fixtures.ts` — deterministic, no random drift
- SSE connection established via `installSseController` / `waitForSseOpen` before POST — no ordering hazard

---

## 5. Reviewer Verification Record (Claude)

Reviewer: Claude (parent task `FE-INT-GATE-C04`, reviewer role)  
Review file: `.orchestrator/reviews/FE-INT-GATE-C04-review-claude.md`  
Verdict: **APPROVE**

Commands run during review:

```bash
git diff --check -- execute-plans/e2e/16-audit-correlation.spec.ts .orchestrator/reviews/FE-INT-GATE-C04-review-claude.md
# Result: passed

NODE_PATH=/home/lupin/code/execute-plans/node_modules \
  /home/lupin/code/execute-plans/node_modules/.bin/esbuild \
  /home/lupin/code/pantheon/execute-plans/e2e/16-audit-correlation.spec.ts \
  --bundle --format=esm --platform=node --external:@playwright/test \
  --outfile=/tmp/fe-int-gate-c04-audit-correlation.mjs
# Result: passed

NODE_PATH=/home/lupin/code/execute-plans/node_modules \
  /home/lupin/code/execute-plans/node_modules/.bin/playwright test \
  e2e/16-audit-correlation.spec.ts --list
# Result: 2 tests found

NODE_PATH=/home/lupin/code/execute-plans/node_modules \
  /home/lupin/code/execute-plans/node_modules/.bin/playwright test \
  e2e/16-audit-correlation.spec.ts --reporter=line \
  --output=/tmp/fe-int-gate-c04-playwright-results
# Result: 2 passed
```

---

## 6. Code Quality Notes (non-blocking)

- `correlationId` included in request body as well as header; harness reads header, so body field is unused (belt-and-suspenders, no issue)
- `SNAPSHOT_AT` declared locally at `15:10` vs `fixtures.ts` at `14:30`; spec does not import from fixtures, no conflict
- `auditEntryFromResponse` handles both `auditEvent` and `audit_event` keys per BFF dual-field convention
- `normalizeSourceMode` defaults non-mock to `"live"` — correct fallback
- Badge TTL is 600 ms; `expect.poll` timeout is 3 000 ms — ample margin

---

## 7. Parent Task Lifecycle

| Stage | Status |
|---|---|
| Implementation (Codex2) | Complete |
| Review (Claude) | Approved — `.orchestrator/reviews/FE-INT-GATE-C04-review-claude.md` |
| Finalization (Codex2) | Complete — `support/sidecars/FE-INT-GATE-C04/finalization-fe-int-gate-c04-codex2.md` |
| Parent task status | `review_approved` (awaiting `done` by Codex2) |

---

## 8. Sidecar Constraints

This sidecar is a support artifact only:
- Does not modify `execute-plans/e2e/16-audit-correlation.spec.ts` or any canonical truth
- Does not modify `ai-status.json`, planning session files, or L1 policy files
- May be absorbed into the parent task close-out packet by the parent owner at their discretion

---

## 9. Handoff to Codex2

**Action required:** Review this packet for completeness and accuracy.

If the packet accurately represents the delivered state and evidence, approve via:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/FE-INT-GATE-C04/FE-INT-GATE-C04-SIDECAR-REVIEW.md \
  ./scripts/ai-status.sh approve FE-INT-GATE-C04-SIDECAR-REVIEW \
  "Sidecar review packet verified and approved"
```

If corrections are needed, reopen with specific required changes via `reopen`.
