# LOOP-PROD-PLANNING-BRIEFS-001

## Purpose

Publish the central planner's active execution instructions into `dev` so every
clean fleet worktree receives the same task scope, stop gates, acceptance tests,
review roles, and postmerge requirements. This task is planning-only.

## Pull request under review

- PR: #3759
- Final candidate head: the exact current PR head named in the central task
  `next` field at review-dispatch time
- Owner: Codex (central planner)
- Reviewer: Antigravity
- Auto-merge must remain disabled.

Compare the live PR head with the current central task note before reviewing.
Never approve an older head and never copy a head from a task-worktree snapshot.

## Exact scope

The PR must contain exactly these nine execution briefs plus this review brief:

1. `loop_prod_archive_closure_repair_dispatch_001.md`
2. `loop_prod_done_guardrail_repair_001.md`
3. `loop_prod_runtime_boot_corrective_001.md`
4. `loop_prod_seq_reconcile_001.md`
5. `ops_deploy_workflow_guard_001.md`
6. `ops_lease_read_after_write_pin_001.md`
7. `ops_reconciliation_json_store_integrity_001.md`
8. `ops_worktree_central_status_root_corrective_001.md`
9. `ops_worktree_central_status_root_postmerge_001.md`

10. `loop_prod_planning_briefs_001.md` — this review-routing brief

## Required review

- Confirm no product source, workflow implementation, runtime evidence, live
  state, task archive, or canonical task status is modified.
- Confirm the nine briefs agree on owner/reviewer separation, disabled
  auto-merge, current-dev composition, exact-head review, and postmerge proof.
- Confirm the central status-root brief explicitly requires recomposing any dev
  advance and a new exact-head approval before merge.
- Confirm PR #3757 is explicitly recorded as an unreviewed merge that cannot
  open the deploy gate without Claude's exact-merge audit.
- Confirm PR #3758 is rejected until it has a deterministic two-writer lost
  update reproduction and separate pre-replace fsync-failure preservation test.
- Confirm the 16 closure repair IDs are the exact IDs emitted by the accepted
  archive audit and that withdrawn names cannot be materialized.
- Confirm the sequencing brief preserves all 48 tasks and requires permissive
  paper-trade signal-to-order-to-fill-to-telemetry-to-loop proof before strict
  auth hardening.
- Run `git diff --check`, inspect the complete PR file list, and record the exact
  reviewed head through the central status command.

## Non-goals

- Do not implement or edit product code.
- Do not deploy or touch live services.
- Do not approve or close any implementation task from this review.
- Do not enable auto-merge.
