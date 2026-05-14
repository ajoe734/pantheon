# FE-INT-GATE-D02 Sidecar Review Packet

- **Packet type:** review_packet (sidecar support artifact)
- **Sidecar task:** FE-INT-GATE-D02-SIDECAR-REVIEW
- **Parent task:** FE-INT-GATE-D02 - F11 new: Handoff reopen SLA
- **Prepared by:** Codex2
- **Reviewer:** Codex
- **Date:** 2026-05-14
- **Parent terminal status:** `done`
- **Helper kind:** review_packet

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-D02 |
| Title | F11 new - Handoff reopen SLA |
| Owner | Codex |
| Reviewer | Claude |
| Status | `done` (archived 2026-05-14T00:54:24Z) |
| Branch | `backend-dev-publish-20260429` |
| Commit | `fcf53de0c07cad99835bb1c2c128f569f8298c0d` |
| Commit subject | `FE-INT-GATE-D02 close handoff SLA spec` |

### Task Description

FE-INT-GATE-D02 added the F11 Handoff Reopen SLA Playwright contract spec at
`execute-plans/e2e/11-handoff-sla.spec.ts`. The spec uses a local BFF/SSE
harness to cover default reopen SLA preservation, fail-closed SLA reset without
approval evidence, and browser-visible SlaSegment append behavior after an
approved reset.

---

## 2. Artifacts and Evidence Sources

| Source | Purpose |
|---|---|
| `execute-plans/e2e/11-handoff-sla.spec.ts` | Primary parent artifact |
| `.orchestrator/reviews/FE-INT-GATE-D02-review-claude.md` | Parent reviewer approval |
| `ai-task-archive/tasks/FE-INT-GATE-D02.json` | Terminal delivery snapshot and handoff history |
| Commit `fcf53de0c07cad99835bb1c2c128f569f8298c0d` | Task-scoped parent closeout commit |

The parent closeout commit changed only:

- `.orchestrator/reviews/FE-INT-GATE-D02-review-claude.md`
- `execute-plans/e2e/11-handoff-sla.spec.ts`

---

## 3. Acceptance Criteria Verification

| # | Criterion | Outcome | Evidence |
|---|---|---|---|
| 1 | reopen 不重設 SLA | PASS | Test `reopen defaults to preserving the original SLA deadline` posts a default reopen request, expects HTTP 202, verifies `currentSlaDueAt` stays at `ORIGINAL_DUE_AT`, `resetCount` stays `0`, the new reopen segment keeps the original due date with `resetSla=false`, and a `handoff.reopened` SSE event reports `resetSla=false`. |
| 2 | reset SLA 無 approval 回 APPROVAL_REQUIRED | PASS | Test `reset SLA without approval evidence returns APPROVAL_REQUIRED` posts `resetSla=true` without approval evidence, expects HTTP 409 with `error.code=APPROVAL_REQUIRED`, `details.kind=approval`, `requires_approval=true`, then checks the harness snapshot is unchanged: original due date, reset count `0`, and only the initial segment remains. |
| 3 | SlaSegment 追加可見 | PASS | Test `approved reset appends a visible SlaSegment` renders the initial segment in the browser, posts an approved reset with `approvalId=approval-reset-f11-001`, verifies the new due date and reset count, rerenders the DOM, and checks the reset segment, due date, approval id, reason, and two visible `[data-testid='sla-segment']` rows. |

---

## 4. Technical Evidence Detail

### 4.1 Local BFF/SSE Harness

`HandoffSlaHarness` starts a local `node:http` server on port 0 and serves:

- `/test-shell` for the Playwright browser context
- `/bff/events/stream?channel=handoff` with SSE headers
- `GET /bff/handoffs/{handoffId}`
- `POST /bff/handoffs/{handoffId}/reopen`

The harness stores the handoff state, SLA segments, audit events, request
records, and open SSE responses in memory. `beforeEach` starts a new harness and
`afterEach` stops it, closing any open SSE responses.

### 4.2 Default Reopen Path

The default reopen path appends a `kind: "reopen"` segment with `resetSla=false`
and keeps both `currentSlaDueAt` and `sla.dueAt` at the original due date. The
test verifies the response shape, segment content, and the published
`handoff.reopened` SSE payload.

### 4.3 Approval-Required Reset Path

When `resetSla=true` is requested without an approval id, the harness returns
HTTP 409 with `APPROVAL_REQUIRED`, `retryable=false`, `userActionable=true`, and
approval-specific details. The test then reads the in-memory snapshot to prove
the rejected request did not mutate SLA state.

### 4.4 Approved Reset and Visible Segment

The approved reset path appends `sla-segment-reset-f11-001`, advances the due
date to `RESET_DUE_AT`, increments reset count to `1`, and carries approval and
reason fields in both snake_case and camelCase aliases. The browser rendering
helper reads back the handoff detail route and renders each segment as
`data-testid="sla-segment"` for visibility assertions.

---

## 5. Parent Review Decision

**Reviewer:** Claude
**Decision:** APPROVED
**Review file:** `.orchestrator/reviews/FE-INT-GATE-D02-review-claude.md`

Claude's review records all acceptance criteria as passing and calls out these
specific quality points:

- `HandoffSlaHarness` provides a self-contained BFF/SSE contract test harness
  with proper lifecycle handling.
- Both snake_case and camelCase field variants are handled consistently.
- Test 1 validates full round-trip behavior including SSE event arrival and
  payload shape.
- Test 2 verifies both the 409 error body and unchanged server state after the
  rejected reset request.
- Test 3 renders SlaSegment rows in-browser and verifies visible text/count.
- Owner-reported verification included esbuild, `git diff --check`,
  Playwright list, and focused Playwright execution.

---

## 6. Verification Evidence

Commands recorded in the parent delivery metadata:

```bash
Playwright --list found 3 tests in /tmp/pantheon-d02-pw using /home/lupin/code/execute-plans/node_modules
focused Playwright run passed 3/3
git diff --cached --check passed
```

Additional owner handoff metadata also records:

```bash
esbuild bundle passed
git diff --check passed
Playwright --list found 3 tests
Playwright single-file run passed 3/3
```

Sidecar packet preparation checks:

```bash
git show --stat --oneline --decorate --no-renames fcf53de0
git show --name-status --format=fuller --no-renames fcf53de0 -- execute-plans/e2e/11-handoff-sla.spec.ts .orchestrator/reviews/FE-INT-GATE-D02-review-claude.md
rg -n "^test\\(|test\\(" execute-plans/e2e/11-handoff-sla.spec.ts
git diff --check -- support/sidecars/FE-INT-GATE-D02/FE-INT-GATE-D02-SIDECAR-REVIEW.md
```

Recorded result from parent archive: Playwright listed 3 tests and the focused
Playwright run passed 3/3.

---

## 7. Delivery Metadata

| Field | Value |
|---|---|
| Parent branch | `backend-dev-publish-20260429` |
| Parent commit | `fcf53de0c07cad99835bb1c2c128f569f8298c0d` |
| Parent commit author | `Codex <codex@pantheon.local>` |
| LLM-Agent metadata | `Codex` |
| Task-ID metadata | `FE-INT-GATE-D02` |
| Reviewer metadata | `Claude` |
| Remote | `origin` |
| Upstream | `origin/backend-dev-publish-20260429` |
| Push status at archive snapshot | `ahead` |

The parent task was finalized as `done` with terminal outcome `completed`.

---

## 8. Sidecar Scope Confirmation

This sidecar packet:

- Does not modify `execute-plans/e2e/11-handoff-sla.spec.ts`
- Does not modify L1 canonical truth
- Does not modify runtime, registry, governance, or contract-source
  implementations
- Does not change parent delivery metadata
- Only adds this support artifact under `support/sidecars/FE-INT-GATE-D02/`

---

## 9. Handoff to Codex

This packet is ready for Codex review. Please confirm:

1. The parent evidence sources listed above are sufficient and internally
   consistent.
2. The acceptance criteria mapping accurately reflects
   `execute-plans/e2e/11-handoff-sla.spec.ts`.
3. Claude's parent review approval is represented faithfully.
4. The sidecar scope stayed limited to support material and did not mutate
   canonical truth or runtime implementation.

Upon approval, return this sidecar task to Codex2 for normal closeout.

---

## 10. Owner Closeout Note

**Closeout owner:** Codex2
**Closeout timestamp:** 2026-05-14T01:32:13Z
**Reviewer approval:** Codex approved this packet on 2026-05-14T01:26:44Z.

Finalization scope remains support-only. The task-owned closeout change is this
packet under `support/sidecars/FE-INT-GATE-D02/`; no L1 canonical truth,
contract source, runtime, registry, governance, parent implementation, or parent
delivery metadata was edited.

Closeout verification commands:

```bash
jq '.tasks[] | select(.id=="FE-INT-GATE-D02-SIDECAR-REVIEW")' ai-status.json
sed -n '1,260p' support/sidecars/FE-INT-GATE-D02/FE-INT-GATE-D02-SIDECAR-REVIEW.md
git status --short -- support/sidecars/FE-INT-GATE-D02
git diff --cached --check -- support/sidecars/FE-INT-GATE-D02/FE-INT-GATE-D02-SIDECAR-REVIEW.md
git diff --cached --name-only
```
