# Review: AG-FE-SW-002 — Conversation/result cards + completeness rail

Reviewer: Claude2
Date: 2026-06-22
Outcome: **APPROVED**

## Scope Reviewed

- `execute-plans/src/lib/bff-v1/agora/workshops.ts` — SSE stream + BFF helpers
- `execute-plans/src/agora/components/workshop-card-types.ts` — payload interfaces
- `execute-plans/src/agora/components/StrategyCompletenessRail.tsx` + test
- `execute-plans/src/agora/components/ResearchPlanCard.tsx` + test
- `execute-plans/src/agora/components/ConsultResultCard.tsx` + test
- `execute-plans/src/agora/components/WorkshopCardRenderer.tsx`
- `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` + test

Schema references verified:
- `services/control-plane/specs/agora/v4/workshop_card.schema.json`
- `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json`
- `services/control-plane/specs/agora/strategy_completeness.schema.json`
- `services/control-plane/specs/agora/v4/strategy_readiness.schema.json`

## Findings

### Approved — no blocking issues

1. **Schema alignment (workshop-card-types.ts):** All 12 card types from the schema are
   implemented. Every required field is present; optional fields correctly marked
   optional. `EvidenceRef` enum values match exactly. No invented fields.

2. **WorkshopStreamEvent:** All 24 event types from `workshop_stream_event.schema.json`
   are represented in `WorkshopStreamEventType`. Required envelope fields match the schema.

3. **StrategyCompletenessRail:** Reads `completeness.overall_grade`, `completeness.dimensions`
   (grade: complete/partial/missing), `readiness.gates`, and `readiness.highest_ready_gate`
   — all directly from the schema shape. Next-question section guards on
   `card_type === "next_question"` before casting.

4. **ResearchPlanCard / ConsultResultCard:** Render all spec fields conditionally.
   `ConsultResultCard` correctly uses `payload.status` (not `card.status`) for the
   status badge and freshness display.

5. **WorkshopCardRenderer:** All 12 `card_type` cases handled; `FallbackCard` safely
   handles unknowns (including `EvidenceSummary`/`BacktestResult` which are not in the
   schema enum). No invented routes or capabilities.

6. **StrategyWorkshopPage:** `useReducer` with UPSERT gives idempotent card updates.
   SSE teardown is wired correctly. Cards sorted by `sequence_no` for display.
   `nextQuestion` derived from highest `sequence_no` among `next_question` cards.
   41 tests pass per commit.

7. **Boundary compliance:** No `RuntimeBinding` writes, no order routes, no capability
   allowlist expansions, no invented schema fields or widget types.

### Non-blocking observations (follow-up acceptable)

1. **`workshop.next_question.updated` not wired to `refreshCards`.**
   This event falls into the `default` branch with no action. If the backend emits
   this as the sole trigger when a new next_question card appears, the rail won't
   update live via SSE. In practice `workshop.snapshot` and
   `workshop.servant.response.completed` typically accompany it. Low risk; address
   in the next iteration.

2. **Approve/Reject buttons in `ResearchPlanCard` have no `onClick` handlers.**
   The buttons render correctly when `allowed_actions.approve/reject` is true, but
   clicking them is a no-op. No BFF action route is yet defined for these, so this
   is an acceptable deferred scope item. A follow-up task should add `onApprove` /
   `onReject` props once the BFF endpoint is available.

## Verdict

Implementation is correct and spec-aligned. Scope is appropriately narrow — no
invented capabilities, no schema drift, no unsafe boundaries crossed.

Returning to owner (Claude) for closeout.
