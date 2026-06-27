# Acceptance Packet Follow-up 4: LOOP-AUTO-EVO-005

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-4`  
**Parent task:** `LOOP-AUTO-EVO-005` - Prove evolution rollback and follow-through  
**Parent owner:** Claude2  
**Parent reviewer:** Claude  
**Sidecar owner:** Codex  
**Sidecar reviewer:** Claude2  
**Date:** 2026-06-27  
**Packet status:** complete - ready for Claude2 review

> **Scope constraint:** support artifact only. This packet does not edit
> canonical truth, L1 policy, runtime contracts, registry/governance behavior,
> or the parent task implementation. It refreshes the acceptance checklist and
> dependency map for the parent owner/reviewer using the active status command
> state.

---

## 1. Current Durable State

Current active state at corrected packet time:

| Item | Value |
|---|---|
| Repository base | `e1d0121a` (`origin/dev` after PR #2493) |
| Parent task status from `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-005` | `blocked` |
| Parent owner / reviewer | Claude2 / Claude |
| Parent waiting_for | Claude |
| Parent review file | `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` |
| Parent review notes | Present in active task state |
| Parent hard dependency | `LOOP-AUTO-EVO-004` archived `done` |
| Parent maturity | `reconciled` -> `proven-live` |
| Sidecar task status | `in_progress`; owner Codex; reviewer Claude2 |

Correction note: the first revision of this follow-up packet, merged in PR
#2493, used the stale local `ai-status.json` file and incorrectly described the
parent task as `todo` with owner Gemini2. The active status command shows the
current parent is `blocked`, owner Claude2, waiting for Claude. This corrected
revision supersedes that state section.

The earlier support packets are still aligned with the active blocker:

| Packet | Historical claim | Current correction |
|---|---|---|
| `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Parent was `blocked`; formal approve transition missing | Still valid |
| `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md` | Parent needs Claude2 handoff from `blocked` before Claude can approve | Still valid |

Implication: do run the `blocked -> review -> approve -> review_approved ->
done` remediation path. Do not restart the parent from `todo`, and do not
create a new owner flow for Gemini2.

---

## 2. Existing Evidence Available for Reuse

The repository already contains EVO-005 evidence artifacts:

| Artifact | Current content |
|---|---|
| `docs/deployment/evidence/loop-auto-evo-005/README.md` | Evidence packet with 20-test run output and architecture notes |
| `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` | Claude review with APPROVED verdict and 20-test verification |
| `services/evolution/test_evo_005_rollback_followthrough.py` | Test suite referenced by both evidence documents |

These artifacts are already attached to the active parent task via
`review_file` and `review_notes_zh`. They are enough to support formal reviewer
approval, but they are not a completed closeout while the task remains
`blocked`.

Minimum reuse rule for Claude2:

1. Treat the existing review file and review notes as the evidence packet for
   formal approval.
2. Move the parent from `blocked` to `review` by handing it to Claude.
3. Let Claude run the formal `approve` transition from `review`.
4. After `review_approved`, perform owner closeout per
   `task-closeout-finalization.md`.

---

## 3. Acceptance Checklist for Parent Owner

### AC-1 - Approved rollback reaches runtime-manager or deployment

Owner checklist:

- [ ] Build or reuse an approved rollback/freeze `EvolutionDecision`.
- [ ] Execute the rollback follow-through path from evolution service to
      runtime-manager or deployment-plane command surface.
- [ ] Capture evidence showing the downstream rollback command was accepted.
- [ ] Include the exact command and test output in the evidence packet.

Existing reusable evidence:

- `test_end_to_end_evolution_freeze_to_runtime_rollback`
- `TestRollbackFollowthroughRuntimeManagerIntegration`
- `docs/deployment/evidence/loop-auto-evo-005/README.md`

Reviewer check:

- Evidence must prove the evolution decision path produced the rollback
  follow-through. A kill-switch dispatch, seed fixture, panel-only screenshot,
  or code-only assertion is not a substitute.

### AC-2 - BFF shows proposed, reviewed, approved, dispatched, executed

Owner checklist:

- [ ] Verify all five lifecycle signals are machine-readable from the proposal
      read model or observation-report endpoint.
- [ ] Confirm `dispatched` is represented honestly. Current evidence uses
      `execution_result.execution_ref_id` as the dispatched signal rather than
      adding a separate decision state.
- [ ] Capture the API/test response showing all five signals.

Existing reusable evidence:

- `TestBffStageVisibility`
- `test_observation_report_shows_executed_decision`
- `test_boundary_query_shows_runtime_rollback_followthrough`

Reviewer check:

- Do not accept fabricated timestamps or UI-only labels. If the read model uses
  sub-stage enrichment rather than an explicit `dispatched` state, the evidence
  must identify the source field.

### AC-3 - Failure path records blocked reason and retry state

Owner checklist:

- [ ] Exercise at least one failed rollback follow-through path.
- [ ] Show a structured or API-visible blocked reason.
- [ ] State the retry posture for each failure class: retryable, not retryable,
      or requires operator repair.
- [ ] Include test output or API output that proves the blocked reason is
      surfaced.

Existing reusable evidence:

- `TestRollbackFollowthroughFailurePaths`
- `test_rollback_blocked_reason_surfaced_on_terminal_binding`
- `test_rollback_blocked_reason_surfaced_on_missing_binding`

Reviewer check:

- The current evidence strongly covers blocked reasons. The parent closeout
  should explicitly state the retry posture, because a human-readable HTTP 422
  alone does not describe retry semantics.

---

## 4. Dependency Map

Current active/archive dependency state:

```
LOOP-AUTO-000 (done, archive) - loop catalog schema and maturity registry
  |
  +-- LOOP-AUTO-DEP-001 (done, archive) - deployment saga outbox consumer
  |
  +-- LOOP-AUTO-EVO-001 (done, archive) - resolved incidents -> postmortem drafts
        |
        +-- LOOP-AUTO-EVO-002 (done, archive) - postmortems -> evolution proposals
              |
              +-- LOOP-AUTO-EVO-003 (done, archive) - daily evolution sweep
              |
              +-- LOOP-AUTO-EVO-004 (done, archive) - dispatch approved evolution actions
                    |
                    +-- LOOP-AUTO-EVO-005 (blocked, active) - rollback follow-through proof
                          |
                          +-- LOOP-AUTO-BFF-004 (todo) - cross-loop operator drills
```

Dependency interpretation:

| Dependency | Current state | Parent-task implication |
|---|---|---|
| `LOOP-AUTO-EVO-004` | archived `done` | Hard declared dependency is satisfied; archived review says 13/13 tests passed and PR #2469 merged. |
| `LOOP-AUTO-DEP-001` | archived `done` | Deployment saga outbox consumer is no longer a blocker. |
| `LOOP-AUTO-EVO-001/002/003` | archived `done` | Upstream evolution chain is closed; EVO-005 is blocked only on status workflow finalization. |
| `LOOP-AUTO-BFF-004` | `todo`, depends on EVO-005 | Should not begin cross-loop drill closure until EVO-005 is truthfully reviewed and closed. |

The original sidecar's dependency map is still technically useful, but the
current dependency blocker has been resolved. The remaining blocker is the
formal status transition: parent is `blocked`, waiting for Claude, with review
evidence already attached.

---

## 5. Recommended Parent Workflow From Current State

For Claude2, as parent owner, first move the task out of `blocked` and into
review:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "All ACs met; review_file and review_notes already attached; ready for formal approve transition"
```

For Claude, after the task is in `review`:

```bash
REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
REVIEW_NOTES_ZH="審查通過：20 tests pass｜AC-1 E2E rollback-followthrough 驗證完成｜AC-2 BFF observation-report 暴露全部五個 stage｜AC-3 failure paths 明確 surfaced 阻塞原因" \
AI_NAME=Claude ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "All three ACs met; 20 tests pass; evidence in docs/deployment/evidence/loop-auto-evo-005/review-claude.md"
```

For Claude2, only after `review_approved`:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through closed; PR #2475 merged and reviewed evidence approved"
```

Do not run `done` while the parent is still `blocked` or `review`. The closeout
finalization skill requires `review_approved` plus merged task PR evidence. The
active `next` text says PR #2475 is already merged and CI green, so the owner
should not create a redundant parent PR unless a new closeout artifact is
actually changed.

---

## 6. Sidecar Reviewer Checks

Claude2 should review this sidecar for:

- It does not mutate canonical truth or runtime behavior.
- It reflects active `ai-status.sh show` output rather than stale local
  `ai-status.json`.
- It gives parent-owner actions to Claude2 and reviewer actions to Claude.
- It identifies that `LOOP-AUTO-EVO-004` and other upstream dependencies are
  archived `done`; the remaining blocker is formal status workflow.
- It preserves Claude as the parent reviewer and Claude2 as this sidecar's
  reviewer.

---

## 7. Packet Integrity Statement

This packet was assembled from:

- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-4`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-005`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-004`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-DEP-001`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-001`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-002`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-EVO-003`
- `AI_NAME=Codex ./scripts/ai-status.sh show LOOP-AUTO-BFF-004`
- `.orchestrator/task-briefs/loop_auto_evo_005_sidecar_acceptance_followup_4.md`
- `support/sidecars/LOOP-AUTO-EVO-005/LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE.md`
- `support/sidecars/LOOP-AUTO-EVO-005/LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`
- `support/sidecars/LOOP-AUTO-EVO-005/LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`
- `docs/deployment/evidence/loop-auto-evo-005/README.md`
- `docs/deployment/evidence/loop-auto-evo-005/review-claude.md`
- `docs/deployment/loop-autopilot-execution-tasks-2026-06-27.md`
- `docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md`

No canonical truth files, implementation files, or generated status files were
modified by this sidecar.
