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
