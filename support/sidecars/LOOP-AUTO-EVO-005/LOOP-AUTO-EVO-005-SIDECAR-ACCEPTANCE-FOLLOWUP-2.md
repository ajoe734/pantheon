# Acceptance Packet Follow-up: LOOP-AUTO-EVO-005

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-2`
**Parent task:** `LOOP-AUTO-EVO-005` — Prove evolution rollback and follow-through
**Parent owner:** Claude2
**Parent reviewer:** Claude
**Prepared by:** Claude
**Date:** 2026-06-27
**Packet status:** complete — ready for Claude2 review

> **Scope constraint:** support artifact only. This packet does not edit canonical truth,
> L1 policy, runtime contracts, registry/governance behavior, or the parent task's
> implementation. It documents the updated state of LOOP-AUTO-EVO-005 after the original
> sidecar (LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE.md) was reviewed and merged, and provides
> the action path to resolve the current blocker.

---

## 1. Relationship to Original Acceptance Packet

The original acceptance packet (`LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE.md`) was:
- Prepared by Claude, reviewed and approved by Claude2 on 2026-06-27
- Merged via PR #2476 (commit `6f91c3cb`)
- Its technical content (gap analysis, evidence scope, reviewer guardrails) remains valid

This follow-up packet documents the **current state delta** as of 2026-06-27:

| Item | State at original packet | State now |
|---|---|---|
| Parent task (EVO-005) | `todo` | `blocked` (waiting for Claude) |
| EVO-005 review doc | Not yet written | Written and approved in `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` |
| Formal `review_approved` transition | Not run | Not run — this is the blocker |
| Parent task owner | Gemini2 (stated in §2) | Claude2 (per current ai-status.json) |

---

## 2. Current Parent Task State

From `ai-status.json` at time of this packet:

| Field | Value |
|---|---|
| ID | `LOOP-AUTO-EVO-005` |
| Title | Prove evolution rollback and follow-through |
| Owner | Claude2 |
| Reviewer | Claude |
| Status | `blocked` |
| Waiting for | Claude |
| Next | "PR 2475 merged and CI green; review doc committed in 4e43453b shows Claude APPROVED; formal ai-status.sh approve transition was never run — needs Claude to run approve command before Claude2 can run done" |

---

## 3. Review Evidence Already in Place

The review document `docs/deployment/evidence/loop-auto-evo-005/review-claude.md`
(committed in `4e43453b`) records:

**Reviewer:** Claude | **Verdict:** APPROVED

### Verification run:
```
python3 -m pytest services/evolution/test_evo_005_rollback_followthrough.py -v
20 passed in 3.50s
```

### Acceptance criteria — all met:

| AC | Assessment | Evidence |
|---|---|---|
| AC-1: Approved rollback reaches runtime-manager | ✅ | `test_end_to_end_evolution_freeze_to_runtime_rollback` + 3 strategy tests (replace / pause_then_replace / liquidate_then_replace) |
| AC-2: BFF shows all five stages | ✅ | `TestBffStageVisibility` (8 tests); `test_observation_report_shows_executed_decision` verifies all 5 stage timestamps |
| AC-3: Failure path records blocked reason | ✅ | `TestRollbackFollowthroughFailurePaths` (5 tests) + `TestRollbackFollowthroughRuntimeManagerIntegration` (2 tests) |

No mocks of the service layer; real FastAPI TestClient and real RuntimeManagerService.

---

## 4. Current Blocker Analysis

### Why the task is blocked

The task EVO-005 is in `blocked` state with `waiting_for: Claude`. This occurred because:

1. Claude2 (owner) put the task into `blocked` state to signal that the formal state-machine
   approval transition was missing — the review document exists and records APPROVED, but the
   `ai-status.sh approve` command was never run while the task was in `review` state.

2. The `approve` command requires the task to be in `review` state
   (`scripts/ai_status.py:4035`). The task is currently in `blocked`, so the direct approve
   path is not available.

### Resolution sequence

The correct resolution path requires **two agents** acting in order:

**Step 1 — Claude2 (owner) runs `handoff`:**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "All acceptance criteria met; 20 tests pass; review doc at docs/deployment/evidence/loop-auto-evo-005/review-claude.md shows Claude APPROVED — ready for formal approve transition"
```

This moves the task from `blocked` → `review` and records the handoff to Claude.

**Step 2 — Claude (reviewer) runs `approve`:**

```bash
REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
REVIEW_NOTES_ZH="審查通過：20 tests pass｜AC-1 E2E rollback-followthrough 驗證完成｜AC-2 BFF observation-report 暴露全部五個 stage｜AC-3 failure paths 明確 surfaced 阻塞原因" \
AI_NAME=Claude ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "All three ACs met; 20 tests pass in test_evo_005_rollback_followthrough.py; evidence in docs/deployment/evidence/loop-auto-evo-005/review-claude.md"
```

This moves the task from `review` → `review_approved`.

**Step 3 — Claude2 (owner) runs closeout per task-closeout-finalization.md:**

After `review_approved`, Claude2 follows the standard PR + done flow:
```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
# wait for PR to merge into dev
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 "Closeout complete"
```

---

## 5. Dependency Map (Updated)

All upstream dependencies of EVO-005 remain in `todo` state. The evidence
approach (hand-crafted approved decisions) described in the original packet's §3
pre-condition note is confirmed as the correct path. The original packet's
dependency map (§3) is still accurate.

**Summary:** No upstream dependency has been resolved since the original packet.
The parent task produced its own evidence by constructing an approved rollback
decision directly, which is the valid workaround documented in the original packet.

---

## 6. No Changes to Original Packet Conclusions

The technical analysis in the original acceptance packet remains fully valid:

- The AC-2 gap (missing `dispatched` state) was addressed by the parent task owner
  (Option B: sub-stage enrichment via `execution_result.execution_ref_id` in the
  observation-report BFF endpoint)
- The AC-3 gap (no `blocked_reason` write-back) was addressed via the failure
  path tests in `TestRollbackFollowthroughFailurePaths`
- Reviewer guardrails G-1 through G-6 were all observed in the review

---

## 7. Packet Integrity Statement

This packet was assembled on 2026-06-27 from the following sources:

- `ai-status.json` (live task state for LOOP-AUTO-EVO-005)
- `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` (existing review doc)
- `support/sidecars/LOOP-AUTO-EVO-005/LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE.md` (original packet)
- `scripts/ai_status.py` (state machine command analysis, lines 4024–4061)

No canonical truth files were modified during this sidecar's execution.
