# FE-INT-GATE-D03 Sidecar Review Packet

- **Packet type:** review_packet (sidecar support artifact)
- **Sidecar task:** FE-INT-GATE-D03-SIDECAR-REVIEW
- **Parent task:** FE-INT-GATE-D03 - F13 new: Agora signal ask journal
- **Prepared by:** Codex2
- **Reviewer:** Copilot
- **Date:** 2026-05-14
- **Parent terminal status:** `done`
- **Helper kind:** review_packet

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-D03 |
| Title | F13 new - Agora signal ask journal |
| Owner | Codex2 |
| Reviewer | Claude |
| Status | `done` (archived 2026-05-14T00:59:19Z) |
| Branch | `backend-dev-publish-20260429` |
| Commit | `c0bdaaac66fa736d2640c1f635febcb9ed411618` |
| Commit subject | `FE-INT-GATE-D03 finalize Agora contract spec` |

### Task Description

FE-INT-GATE-D03 added the F13 Agora Playwright contract spec at
`execute-plans/e2e/13-agora.spec.ts`. The spec uses a local BFF/SSE harness to
cover signal feedback, Agora ask streaming/transcript readback, and journal
merge-patch atomicity.

---

## 2. Artifacts and Evidence Sources

| Source | Purpose |
|---|---|
| `execute-plans/e2e/13-agora.spec.ts` | Primary parent artifact |
| `.orchestrator/reviews/FE-INT-GATE-D03-review-claude.md` | Parent reviewer approval |
| `ai-task-archive/tasks/FE-INT-GATE-D03.json` | Terminal delivery snapshot and handoff history |
| Commit `c0bdaaac66fa736d2640c1f635febcb9ed411618` | Task-scoped parent closeout commit |

The parent closeout commit changed only:

- `.orchestrator/reviews/FE-INT-GATE-D03-review-claude.md`
- `execute-plans/e2e/13-agora.spec.ts`

---

## 3. Acceptance Criteria Verification

| # | Criterion | Outcome | Evidence |
|---|---|---|---|
| 1 | Signal feedback emits audit + SSE | PASS | Test `signal feedback writes audit evidence and publishes signal SSE` posts to `/bff/agora/signals/{id}/feedback`, expects HTTP 202, verifies the audit entry via `/bff/audit?target_ref=signal:{id}`, then checks `signal.feedback.recorded` and `operator.audit.updated` SSE events. |
| 2 | Ask REST complete transcript can be retrieved | PASS | Test `ask streams deltas and exposes the completed transcript through REST` posts to `/bff/agora/ask`, waits for two `ask.message.delta` events plus `ask.message.completed`, reconstructs the assistant message, then reads `/bff/agora/ask/sessions/{id}` and checks the completed transcript. |
| 3 | Journal PATCH uses `application/merge-patch+json` and is atomic | PASS | Test `journal PATCH uses merge-patch media type and rejects invalid patches atomically` verifies the valid PATCH content type and version bump, then sends an invalid outcome and checks HTTP 422 with `details.atomic=true`; final readback confirms the rejected patch did not mutate state. |
| 4 | SSE delta unavailable path can skip | PASS | `ASK_SSE_AVAILABLE` is controlled by `F13_AGORA_ASK_SSE_AVAILABLE !== "0"`, and the ask-streaming test calls `test.skip(!ASK_SSE_AVAILABLE, ...)`. |

---

## 4. Technical Evidence Detail

### 4.1 Local BFF/SSE Harness

`AgoraHarness` starts a local `node:http` server on port 0 and serves:

- `/test-shell` for the Playwright browser context
- `/bff/events/stream?channel=signal|audit|ask` with SSE headers
- `/bff/agora/signals/{id}/feedback`
- `/bff/audit?target_ref=...`
- `/bff/agora/ask`
- `/bff/agora/ask/sessions/{id}`
- `/bff/agora/journal/{id}`

The harness stores audit entries, ask sessions, journal state, request records,
and open SSE responses in memory. `afterEach` closes browser EventSources and
stops the server.

### 4.2 Signal Feedback Path

The feedback handler records the incoming request, appends an audit entry with
`action: "agora.signal_feedback.recorded"`, returns a dual snake/camel response,
publishes `signal.feedback.recorded`, and publishes `operator.audit.updated`.
The test verifies the response, audit readback, and both SSE event families.

### 4.3 Ask Streaming and REST Transcript

The ask handler writes a deterministic completed session, publishes
`ask.session.started`, two `ask.message.delta` events, and
`ask.message.completed`. The browser client records those events, the test
reconstructs the assistant content from deltas, and the REST readback must expose
the same assistant message in the completed transcript.

### 4.4 Journal Merge-Patch Atomicity

`patchJournal` always sends `Content-Type: application/merge-patch+json`. The
harness rejects unsupported media types with HTTP 415 and invalid outcomes with
HTTP 422. The invalid-outcome path returns `details.atomic=true` and does not
apply partial changes; readback remains at the valid patch result.

---

## 5. Parent Review Decision

**Reviewer:** Claude
**Decision:** APPROVED
**Review file:** `.orchestrator/reviews/FE-INT-GATE-D03-review-claude.md`

Claude's review records all acceptance criteria as passing and calls out these
specific quality points:

- `AgoraHarness` uses an ephemeral HTTP server, channel tracking, and teardown.
- Typed response aliases make assertions readable.
- `installAgoraClient` sets up EventSource channels and exposes browser helpers.
- `waitForSseOpen` and `expect.poll` handle async SSE setup before actions.
- Invalid journal patch assertions verify both error shape and state idempotency.

---

## 6. Verification Evidence

Commands recorded in the parent delivery metadata:

```bash
NODE_PATH=/home/lupin/.npm/_npx/8d2e8001be657ecc/node_modules \
  /home/lupin/.npm/_npx/8d2e8001be657ecc/node_modules/.bin/playwright \
  test e2e/13-agora.spec.ts --list

npx esbuild e2e/13-agora.spec.ts \
  --bundle --platform=node --format=esm \
  --external:@playwright/test \
  --outfile=/tmp/fe-int-gate-d03-agora.mjs

NODE_PATH=/home/lupin/.npm/_npx/8d2e8001be657ecc/node_modules \
  /home/lupin/.npm/_npx/8d2e8001be657ecc/node_modules/.bin/playwright \
  test e2e/13-agora.spec.ts
```

Recorded result: Playwright listed 3 tests, esbuild was clean, and the focused
Playwright run passed 3/3 after Chromium cache installation.

Sidecar packet preparation checks:

```bash
git show --stat --format=fuller c0bdaaac66fa736d2640c1f635febcb9ed411618
git show --name-only --format='%H%n%s%n%b' c0bdaaac66fa736d2640c1f635febcb9ed411618
rg -n "test\\(|test\\.skip|signal.feedback|ask.message|merge-patch|atomic|/bff/agora" execute-plans/e2e/13-agora.spec.ts
```

---

## 7. Delivery Metadata

| Field | Value |
|---|---|
| Parent branch | `backend-dev-publish-20260429` |
| Parent commit | `c0bdaaac66fa736d2640c1f635febcb9ed411618` |
| Parent commit author | `Codex <codex@pantheon.local>` |
| LLM-Agent metadata | `Codex2` |
| Task-ID metadata | `FE-INT-GATE-D03` |
| Reviewer metadata | `Claude` |
| Remote | `origin` |
| Upstream | `origin/backend-dev-publish-20260429` |
| Push status at archive snapshot | `ahead` |

The parent task was finalized as `done` with terminal outcome `completed`.

---

## 8. Sidecar Scope Confirmation

This sidecar packet:

- Does not modify `execute-plans/e2e/13-agora.spec.ts`
- Does not modify L1 canonical truth
- Does not modify runtime, registry, governance, or contract-source implementations
- Does not change parent delivery metadata
- Only adds this support artifact under `support/sidecars/FE-INT-GATE-D03/`

---

## 9. Handoff to Copilot

This packet is ready for Copilot review. Please confirm:

1. The parent evidence sources listed above are sufficient and internally
   consistent.
2. The acceptance criteria mapping accurately reflects
   `execute-plans/e2e/13-agora.spec.ts`.
3. Claude's parent review approval is represented faithfully.
4. The sidecar scope stayed limited to support material and did not mutate
   canonical truth or runtime implementation.

Upon approval, return this sidecar task to Codex2 for normal closeout.

---

## 10. Owner Closeout

Final reviewer state: Codex approved this packet after the chair reassigned the
review from Copilot to Codex because the Copilot lane was paused. The Copilot
references above remain the historical handoff target at packet-preparation
time.

Closeout checks run by Codex2 on 2026-05-14:

```bash
git diff --check -- support/sidecars/FE-INT-GATE-D03/FE-INT-GATE-D03-SIDECAR-REVIEW.md .orchestrator/reviews/FE-INT-GATE-D03-SIDECAR-REVIEW-review-codex.md
test -f support/sidecars/FE-INT-GATE-D03/FE-INT-GATE-D03-SIDECAR-REVIEW.md
test -f .orchestrator/reviews/FE-INT-GATE-D03-SIDECAR-REVIEW-review-codex.md
```

Scope remains support-only: this closeout updates this sidecar packet and keeps
the reviewer approval note in `.orchestrator/reviews/`; it does not change L1
canonical truth, runtime, registry, governance, or parent task implementation
files.
