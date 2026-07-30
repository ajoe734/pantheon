# SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730 — split worker status root from git source root

Owner: `Codex` · Reviewer: `Antigravity` · Phase: Supervisor / Fleet dispatch unblock

This is a supervisor control-plane repair for auto-worker dispatch. It does not
claim Pantheon or Agora product closeout by itself; it removes the blocker that
kept the supervisor from launching downstream workers.

## Problem

The live supervisor runs split-root:

- canonical status, queue, state, and activity files resolve under
  `/home/lupin/pantheon`;
- the supervisor program and command checkout resolve under
  `/home/lupin/pantheon-ci-deploy/dev-root`.

Before this change, `prepare_worker_workspace()` derived the git repository root
from `paths.status_file`. That made worker worktree creation try to write git
metadata under the shared status root:

```text
fatal: could not create directory of '.git/worktrees/l12-verify-obs-001': Read-only file system
```

That is a fleet-level blocker. Dispatch can enqueue tasks, but every worker that
needs an isolated task worktree is rejected before it can claim useful work.

## Fix

`worker_worktrees.source_root` now separates the writable git source checkout
from the supervisor status root:

- `status_root` is still derived from `paths.status_file` and remains the
  authority for status, activity, generated task-brief materialization, and
  runtime lease metadata;
- `workspace_source_root` is used only for git worktree operations:
  existing-worktree lookup, branch-in-root guard, refresh, and
  `git worktree add`;
- pre-lock worker base-ref fetch and worktree cleanup/prune use the same
  source root, so split-root deployments do not regress on the next lifecycle
  phase after dispatch;
- explicit retry metadata is rejected if it points at either the shared status
  checkout or the configured source checkout, preserving the no-shared-checkout
  worker boundary;
- missing configured source roots fail closed with
  `refresh_status=source_root_missing` instead of falling back to shared root.

The default `source_root` is empty, so single-root deployments retain the old
behavior until they explicitly opt into split-root.

## Validation

Targeted regression:

```bash
PYTHONPATH=.orchestrator python3 -m unittest \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_worker_workspace_allocates_task_worktree_metadata \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_worker_workspace_uses_configured_source_root_for_git_worktree \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_github_retry_allocates_isolated_worktree_outside_allowlist \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_github_retry_refuses_shared_checkout_when_worktrees_disabled \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_github_retry_rejects_shared_checkout_metadata \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_github_retry_rejects_source_root_checkout_metadata \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_worker_workspace_allocates_chair_review_worktree_metadata \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_worker_workspace_materializes_task_brief_into_isolated_worktree \
  test_supervisor.ProcessQueueDispatchGuardTests.test_prepare_worker_workspace_blocks_dirty_reused_worktree \
  test_supervisor.DispatchStatusSyncTests.test_run_once_fetches_worker_base_before_taking_runtime_lock \
  test_supervisor.PruneOrphanWorktreesTests.test_prune_orphan_worktrees_uses_configured_source_root_for_git_metadata \
  test_supervisor.PruneChairReviewWorktreesTests.test_prune_chair_review_worktrees_uses_configured_source_root_for_git_metadata
# Ran 12 tests ... OK
```

Full supervisor suite:

```bash
PYTHONPATH=.orchestrator python3 .orchestrator/test_supervisor.py
# Ran 461 tests ... OK
```

## Live follow-up after merge

Promote the supervisor command root to a dev commit containing this change, then
set the live supervisor config:

```json
"worker_worktrees": {
  "enabled": true,
  "root": "/tmp/pantheon-worker-worktrees",
  "source_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "base_ref": "origin/dev",
  "reuse_existing": true
}
```

After restart, the acceptance gate is an activity-log proof that the next
isolated dispatch records `workspace_source_root=/home/lupin/pantheon-ci-deploy/dev-root`
and no longer emits `dispatch_blocked_worktree_lease` with
`Read-only file system` for `.git/worktrees`.
