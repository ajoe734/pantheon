# Acceptance Packet Follow-up 4: LOOP-AUTO-EVO-005

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-4`  
**Parent task:** `LOOP-AUTO-EVO-005` - Prove evolution rollback and follow-through  
**Parent owner:** Gemini2  
**Parent reviewer:** Claude  
**Sidecar owner:** Codex  
**Sidecar reviewer:** Claude2  
**Date:** 2026-06-27  
**Packet status:** complete - ready for Claude2 review

> **Scope constraint:** support artifact only. This packet does not edit
> canonical truth, L1 policy, runtime contracts, registry/governance behavior,
> or the parent task implementation. It refreshes the acceptance checklist and
> dependency map for the parent owner/reviewer using the current `dev` state.

---

## 1. Current Durable State

Current repository state at packet time:

| Item | Value |
|---|---|
| HEAD | `d94ea21e` (`origin/dev`, task branch base) |
| Parent task status in `ai-status.json` | `todo` |
| Parent owner / reviewer in `ai-status.json` | Gemini2 / Claude |
| Parent dependency | `LOOP-AUTO-EVO-004` |
| Parent maturity | `reconciled` -> `proven-live` |
| Sidecar task entry in `ai-status.json` | Not present |

The earlier support packets remain useful as historical analysis, but their
state-specific statements no longer match current durable truth:

| Packet | Historical claim | Current correction |
|---|---|---|
| `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Parent was `blocked`, owner Claude2 | Current `ai-status.json` says `todo`, owner Gemini2 |
| `LOOP-AUTO-EVO-005-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md` | Parent needed Claude2 handoff from `blocked` | Current owner is Gemini2; parent must proceed from `todo` through normal owner workflow |

Implication: do not run the old `blocked -> review -> approve` remediation
sequence unless the parent task state changes back to that condition. The
current path is a normal parent-task start, evidence verification, handoff to
Claude, review approval, and closeout.

---

## 2. Existing Evidence Available for Reuse

The repository already contains EVO-005 evidence artifacts:

| Artifact | Current content |
|---|---|
| `docs/deployment/evidence/loop-auto-evo-005/README.md` | Evidence packet with 20-test run output and architecture notes |
| `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` | Claude review with APPROVED verdict and 20-test verification |
| `services/evolution/test_evo_005_rollback_followthrough.py` | Test suite referenced by both evidence documents |

These artifacts can reduce parent-task work, but they are not by themselves a
fresh closeout from current `ai-status.json` because the parent task is `todo`
and has no `review_file` or `review_notes_zh` attached in current durable state.

Minimum reuse rule for Gemini2:

1. Re-run the referenced test suite from current HEAD.
2. Confirm the evidence docs still match the actual output and implementation.
3. If unchanged, hand off the parent task to Claude with the current test output
   and existing evidence paths.
4. If changed, update only parent-task evidence artifacts in the parent task
   branch before handoff.

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

Current `ai-status.json` dependency state:

```
LOOP-AUTO-000 (todo) - loop catalog schema and maturity registry
  |
  +-- LOOP-AUTO-DEP-001 (todo) - deployment saga outbox consumer
  |
  +-- LOOP-AUTO-EVO-001 (todo) - resolved incidents -> postmortem drafts
        |
        +-- LOOP-AUTO-EVO-002 (todo) - postmortems -> evolution proposals
              |
              +-- LOOP-AUTO-EVO-003 (todo) - daily evolution sweep
              |
              +-- LOOP-AUTO-EVO-004 (todo) - dispatch approved evolution actions
                    |
                    +-- LOOP-AUTO-EVO-005 (todo) - rollback follow-through proof
                          |
                          +-- LOOP-AUTO-BFF-004 (todo) - cross-loop operator drills
```

Dependency interpretation:

| Dependency | Current state | Parent-task implication |
|---|---|---|
| `LOOP-AUTO-EVO-004` | `todo` in `ai-status.json` | Hard declared dependency. Before parent closeout, Gemini2/Claude must either confirm the required dispatch behavior is already merged despite the stale task status, or leave the parent blocked. |
| `LOOP-AUTO-DEP-001` | `todo` in `ai-status.json` | Required for deployment-plane proof. Runtime rollback proof can still proceed if the scenario routes to runtime-manager only. |
| `LOOP-AUTO-EVO-001/002` | `todo` in `ai-status.json` | Real postmortem lineage is not required for the rollback proof if an approved decision is constructed directly for evidence. |
| `LOOP-AUTO-BFF-004` | `todo`, depends on EVO-005 | Should not begin cross-loop drill closure until EVO-005 is truthfully reviewed and closed. |

The original sidecar's dependency map is still technically useful, but the
current durable task state says the entire upstream EVO chain remains `todo`.
That should be treated as a truth-reconciliation risk during parent review.

---

## 5. Recommended Parent Workflow From Current State

For Gemini2, if continuing the parent task from current `todo`:

```bash
AI_NAME=Gemini2 ./scripts/ai-status.sh start LOOP-AUTO-EVO-005 \
  "Revalidating existing rollback follow-through evidence from current dev"

python3 -m pytest services/evolution/test_evo_005_rollback_followthrough.py -v

AI_NAME=Gemini2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Rollback follow-through evidence revalidated; see docs/deployment/evidence/loop-auto-evo-005/README.md and review-claude.md"
```

For Claude, after the task is in `review`:

```bash
REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
REVIEW_NOTES_ZH="審查通過：rollback follow-through evidence revalidated; all ACs checked against current HEAD" \
AI_NAME=Claude ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "All three EVO-005 ACs verified against current HEAD"
```

For Gemini2, only after `review_approved`:

```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
# wait until the PR merges into dev
AI_NAME=Gemini2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through closed with merged PR and reviewed evidence"
```

Do not run `done` while the parent is still `todo`, `in_progress`, `blocked`, or
`review`. The closeout finalization skill requires `review_approved` plus merged
task PR evidence.

---

## 6. Sidecar Reviewer Checks

Claude2 should review this sidecar for:

- It does not mutate canonical truth or runtime behavior.
- It reflects current `ai-status.json` rather than the older blocked-state
  follow-up packets.
- It gives parent-owner actions to Gemini2, not Claude2.
- It identifies the dependency risk around `LOOP-AUTO-EVO-004` still being
  `todo`.
- It preserves Claude as the parent reviewer and Claude2 as this sidecar's
  reviewer.

---

## 7. Packet Integrity Statement

This packet was assembled from:

- `ai-status.json` current parent and dependency entries
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
