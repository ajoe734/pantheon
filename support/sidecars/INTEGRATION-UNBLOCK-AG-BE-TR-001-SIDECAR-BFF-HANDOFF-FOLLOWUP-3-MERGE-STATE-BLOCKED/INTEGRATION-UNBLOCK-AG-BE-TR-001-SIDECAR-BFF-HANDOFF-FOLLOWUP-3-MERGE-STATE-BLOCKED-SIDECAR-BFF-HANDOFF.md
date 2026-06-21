# INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED — BFF Handoff Packet

| Field | Value |
|---|---|
| Task ID | `INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED` |
| Parent status | `done` (archived 2026-06-21T20:47:15Z) |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Builds on | `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` (done, PR #2139 merged 2026-06-21T20:32:35Z) |

This packet is a support artifact only. It does not modify L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance implementation, or execute-plans frontend
code. The parent owner (`AG-BE-TR-001` owner: `Claude2`) decides whether and how to absorb
this material.

---

## Context: Merge-State-Blocked Unblock Summary

The integration task
`INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED`
was opened because `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (branch
`task/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`) fell behind `dev` after intermediate
PRs landed while the sidecar was in `review_approved`. GitHub set the PR
merge-state to `BLOCKED` (behind-target protection), preventing auto-merge.

**Resolution (from parent task commit `daf758b6`):**

| Step | Details |
|---|---|
| Root cause | Branch lagged dev; two intermediate PRs landed after the sidecar PR was opened. |
| Fix | Two dev-merge commits (`ed84d214`, `2949913b`) brought the branch forward. |
| PR merge | PR #2139 merged into `dev` on 2026-06-21T20:32:35Z. |
| FOLLOWUP-3 status | Archived `done` at 2026-06-21T20:32:52Z. |
| Parent unblock task | Archived `done` at 2026-06-21T20:47:15Z. |

The merge-state-blocked pattern is an integration infrastructure concern (branch
protection + auto-merge timing), not a content or schema defect. No BFF route code,
schema, or OpenAPI file was changed during the unblock.

---

## Post-Merge State of Deliverables

| Sidecar packet | Status | Key content |
|---|---|---|
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF` | `done` (prior) | BFF query gap matrix, operator journeys A–H, `tradingRoom.ts` method signatures, acceptance checks, open design notes. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | `done` (prior) | Phased implementation sequence, backend module structure, D9 position event fields, Trading Room SSE contract, BFF degraded-response patterns, TypeScript types, safety wording, pending questions Q1–Q5. |
| `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | **done** (PR #2139, 2026-06-21) | Schema-derived corrections to Packet 2 TypeScript types, Q1/Q2/Q4 resolution, `additionalProperties` clarification, idempotency implementation pattern, BFF test structure supplement. |

All three packets are now merged into `dev` and available to the `AG-BE-TR-001`
implementation owner (`Claude2`).

---

## Current BFF State (observed post-merge, 2026-06-21)

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `services/control-plane/bff/agora/trading_room/router.py` | Placeholder; returns empty `APIRouter`. Unchanged by any sidecar. | `AG-BE-TR-001` must implement all Trading Room routes in this file. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Does not exist. | `AG-FE-TR-001` must create this module when `AG-BE-TR-001` routes are live. |
| `AG-BE-TR-001` task | `todo`; owner `Claude2`, reviewer `Codex`. | Sidecar packets are informational; implementation has not started. |
| `AG-BE-CP-001` task | `blocked`. | D8 candidate-to-decision-event promotion remains gated on this task. |
| `AG-XR-OPENAPI-004` | `done` (archived 2026-06-21T13:30:08Z). | v1.3 OpenAPI bundle and v4 schemas are available. This gate is lifted. |

No BFF route implementations, schema changes, or frontend files were modified by the
three sidecar packets or the merge-unblock task.

---

## BFF Query Gap Summary (post-merge, unchanged from FOLLOWUP-3)

All nine Trading Room and Trading Intent BFF gaps from Packet 1 remain unimplemented.
The sidecar series provided decision-support material; the implementation gaps are
unchanged and require `AG-BE-TR-001` to implement them.

| Gap | Route | Owner |
|---|---|---|
| Trading Room aggregate | `GET /bff/agora/trading-room` | `AG-BE-TR-001` |
| Strategy-level detail | `GET /bff/agora/trading-room/strategies/{strategy_id}` | `AG-BE-TR-001` |
| Decision-event queue list | `GET /bff/agora/trading-room/decision-events` | `AG-BE-TR-001` |
| Decision-event detail | `GET /bff/agora/trading-room/decision-events/{decision_event_id}` | `AG-BE-TR-001` |
| Trader decision recording | `POST .../decision-events/{id}/decisions` | `AG-BE-TR-001` |
| Trading Room SSE stream | `GET /bff/agora/trading-room/stream` | `AG-BE-TR-001` |
| Trading Intent read | `GET /bff/agora/trading-intents/{intent_id}` | `AG-BE-TR-001` |
| Governed handoff submission | `POST .../trading-intents/{id}/handoffs` | `AG-BE-TR-001` |
| Handoff withdrawal | `POST .../trading-intents/{id}/withdraw` | `AG-BE-TR-001` |
| Frontend BFF client | `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | `AG-FE-TR-001` (gate on `AG-BE-TR-001`) |

Candidate-to-decision-event promotion (D8 journey) remains gated on `AG-BE-CP-001`.

---

## Operator Journey Context

The merge-unblock scenario itself does not introduce new operator journeys. Journeys A–H
remain as defined in `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF.md`.

The relevant context for the parent owner after the merge-unblock is:

**Operator experience as of today (2026-06-21):** The Trading Room surface does not yet
exist in Agora. No route is implemented in `trading_room/router.py`. An operator opening
the Trading Room tab receives no data — the BFF returns nothing from the placeholder
router. The sidecar series provides the design reference; the journey only becomes
available after `AG-BE-TR-001` implements the routes.

**Post-FOLLOWUP-3 TypeScript corrections the frontend owner must apply** (from
`AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`):

| Correction | Summary |
|---|---|
| `spec_version` | Required field `spec_version: "1.0"` was missing from `TradingDecisionEvent`. |
| `suggested_size` shape | Use `size_hint` + `portfolio_pct`, not `value` + `unit`. |
| `calibration_state` | Enum is `"calibrated" \| "partially_calibrated" \| "uncalibrated"`; `"degraded"` is invalid. |
| `invalidation.current_state` | Enum is `"valid" \| "watch" \| "invalidated"`; `"none"` is invalid. |
| `invalidation.conditions` | Required array (may be `[]`); was missing from Packet 2 types. |
| `origin` | Constrained enum, not free `string`. |
| `suggested_action` | Constrained enum, not free `string`. |
| `rationale` | `minItems: 1`; empty array is schema-invalid. |
| Degradation signalling | `additionalProperties: false` at aggregate root blocks `degradation_notes`; use `staleness_reasons` on strategy entries or `risk_summary.alerts` instead. |
| `top_decision_events` | Optional; omit the key when not populated, do not set to `null`. |
| Q4 response codes | `POST decisions` → `201`; `POST handoffs` → `202`; `POST withdraw` → `200`. |

The corrected `TradingDecisionEvent` TypeScript interface and all schema-derived
corrections are in `support/sidecars/AG-BE-TR-001/AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`.

---

## Frontend Handoff Notes (post-merge)

All frontend handoff material from the three packets is now merged and available.
No new frontend work is unlocked by the merge-unblock itself; the gates are unchanged:

| Frontend task | Gate |
|---|---|
| `AG-FE-TR-001` (Trading Room tab + `tradingRoom.ts`) | `AG-BE-TR-001` must land first. |
| `AG-FE-TR-002` (CandidateReviewDrawer + queue cards) | `AG-FE-TR-001` and `AG-BE-CP-001` must land first. |

When `AG-BE-TR-001` routes are implemented and testable, `AG-FE-TR-001` should:

1. Create `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` with the method
   signatures from Packet 1 (see `AG-BE-TR-001-SIDECAR-BFF-HANDOFF.md`).
2. Use the corrected TypeScript interfaces from Packet 3 (not the Packet 2 versions).
3. Apply the non-binding label rule: `suggested_size` must always show a "non-binding"
   label; never expose size hints as order inputs.
4. Show `confidence` and `probability` as distinct fields (D4 semantics).
5. Follow live-strict BFF fallback posture (per BFF HA policy §5.1); no local fixture
   fallback or synthetic decision events.

---

## Remaining Open Questions (from FOLLOWUP-3, unresolved)

| # | Question | Default if not resolved |
|---|---|---|
| Q3 | Idempotency window for trader decisions? How long should a duplicate `Idempotency-Key` be honoured before expiry? | Process-lifetime (existing BFF pattern). For production: 24 hours is the conventional default. Owner decision required. |
| Q5 | Should the `trading_room.snapshot` SSE event carry the full `TradingRoomAggregate` shape or a lighter payload? | Full aggregate shape recommended (avoids a separate GET on reconnect). Confirm with owner. |
| Q6 | Who populates `position_snapshot` on add/reduce/exit/review events — stored with the event or joined at query time by BFF? | D9 implies stored with event; schema uses `"additionalProperties": true` on `position_snapshot`, leaving source ambiguous. Owner clarification needed. |
| Q7 | Should `decision_state` reflect live intent/handoff lifecycle, or snapshot at time of trader decision? | Most natural: live projection (updating as intent progresses). Confirm update semantics with owner. |

---

## Merge-Unblock Handoff: No Residual Action Required

The merge-state-blocked situation is fully resolved:

- PR #2139 merged 2026-06-21T20:32:35Z.
- `AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` archived `done`.
- `INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED` archived `done`.
- All three sidecar packets are in `dev`; no pending PRs or open review gates.

The only remaining actions are owned by the implementation task `AG-BE-TR-001` (owner:
`Claude2`, reviewer: `Codex`) which is `todo` and gated on `AG-BE-CP-001` (currently
blocked) and `AG-XR-OPENAPI-004` (done).

This packet is ready for reviewer handoff to `Claude`.

---

## Reviewer Handoff

`Claude` review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. No canonical docs, schemas, OpenAPI, BFF runtime, or frontend files were changed by this sidecar. |
| Merge-unblock summary accurate | PR #2139 merged 2026-06-21T20:32:35Z; parent task archived done 2026-06-21T20:47:15Z; root cause (branch behind dev) and fix (two dev-merge commits) correctly stated. |
| BFF state accurate | `trading_room/router.py` remains a placeholder; `tradingRoom.ts` does not exist; `AG-BE-TR-001` is `todo`; `AG-BE-CP-001` is `blocked`; `AG-XR-OPENAPI-004` is `done`. |
| Correction summary accurate | TypeScript corrections listed match those in FOLLOWUP-3 packet (schema-derived, not invented). |
| No canonical mutation | No L1 docs, schemas, OpenAPI, BFF runtime, or frontend source modified. |
| Open questions | Q3/Q5/Q6/Q7 correctly carried forward from FOLLOWUP-3; no resolutions invented. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED/INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="BFF handoff packet for merge-unblock context approved: summarises PR #2139 merge resolution, post-merge BFF state (trading_room router still placeholder, tradingRoom.ts absent), cumulative sidecar packet scope, TypeScript type correction summary from FOLLOWUP-3, remaining open questions Q3/Q5/Q6/Q7, and frontend gate status — all as support material without modifying canonical truth." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED-SIDECAR-BFF-HANDOFF \
  "BFF handoff packet for merge-unblock context approved for AG-BE-TR-001 owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, scope issue, or missing detail needed before approval."
```

---

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/integration_unblock_ag_be_tr_001_sidecar_bff_handoff_followup_3_merge_state_blocked_sidecar_bff_handoff.md

AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED-SIDECAR-BFF-HANDOFF
# in_progress; owner Claude2; reviewer Claude

AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-MERGE-STATE-BLOCKED
# source: archive; terminal_status: done; archived_at 2026-06-21T20:47:15Z

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
# source: archive; terminal_status: done; archived_at 2026-06-21T20:32:52Z

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-TR-001
# todo; owner Claude2; reviewer Codex; depends_on AG-BE-CP-001 (blocked), AG-XR-OPENAPI-004 (done)

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001
# blocked; owner Codex; reviewer Claude2
```
