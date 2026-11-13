# OPS-REOPEN-DISPATCH-IDENTITY-20261112

Source-only repair of two measured blockers in the existing reopen delivery path.

## 1. Dispatch identity
`task_review_reopen_revision` counted `reopen` events in the (rotating) activity tail and mixed that
unstable count into `ready_dispatch_signature`, `build_dispatch_event` and the event payloads. Queued
events (`revision2`) were therefore judged stale (`current1`) before launch although generation, owner,
reviewer, dependencies and `review_requeue_intent_id` were unchanged. The history-count identity and its
callers/tests are removed; the canonical `review_requeue_intent` (pending -> materialized) is the only
reopen identity. Real invalidation (new intent, generation, owner, reviewer, dependency, delivery
binding) is unchanged. Tests: `SharedPlannerContractTests` (shrinking/rotating activity view,
pending->materialized ack, once-only redispatch, new-intent staleness).

## 2. Same-owner WIP resumption
`worker_workspace._same_owner_reopen_wip_may_resume` lets `prepare_worker_workspace` reuse a dirty task
worktree without refresh refusal only when ALL hold: task `in_progress` with a valid canonical review
requeue record; dispatch reason `owned_in_progress_dispatch`; request generation/intent match; target agent
is the canonical owner and not the reviewer; task branch; any existing lease matches path/branch/previous
agent; no other active worker on the path; every dirty path is covered by the task's declared artifacts
(no rename/copy/unmerged entries). No commit, stash, reset, clean or archive happens; only a
non-overwriting `git merge --ff-only` is attempted. Reviewer, other agents, unrelated files, missing
intent, stale generation, wrong status and foreign leases still hit `skipped_dirty_worktree`.
Tests use real temporary Git worktrees (`RecoveryCurrentbaseDeliveryTests`).

## Not done here (original Ops duties)
Source merge only. No live canonical/queue/runtime changes, no runtime promotion, no product acceptance,
no touching of the frontend worktrees. Actual worker receipts for the original QA/pinned tasks remain Ops duties.

## Real-flow regressions (review reopen)

`RecoveryCurrentbaseDeliveryTests` now drives the real `build_dispatch_plan` -> `reserve_dispatch_plan` -> `process_queue` path on a temporary Git worktree with a shrinking activity view (2 -> 1) and unchanged canonical intent. See `evidence.json` `verification.real_flow_regressions` for exact commands, results (3 / 28 / 36 tests OK, exit 0) and limitations. Runtime promotion and original-task worker receipts remain Ops duties.

## Autostash fix (review round 2)

`_resume_owner_wip_worktree` now disables autostash, validates the workspace (no unmerged paths, no new stash, unchanged dirty set) and only then does prepare admit the dirty-guard bypass. Regression: `OwnerWipResumeAutostashTests` (real Git, `merge.autoStash=true`). See `evidence.json` `verification.autostash_fix`.

## Full-pipeline overlapping fast-forward (review round 3)

`test_real_flow_overlapping_ff_wip_not_stashed_or_conflicted` drives the real planner/reserve/`process_queue` path with `merge.autoStash=true`, dev advanced over a file carrying staged+unstaged owner WIP: no stash, no conflict markers, index/worktree/HEAD bytes unchanged, one launch. It fails with `--autostash` restored. `test_worker_workspace`: 64 tests OK.
