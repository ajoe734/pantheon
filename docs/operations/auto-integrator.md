# Auto Integrator Operations Guide

Status: active operations guide for `scripts/git/auto_integrator.py`.
Last updated: 2026-08-28

`scripts/git/auto_integrator.py` is a serialized integration runner that bridges
the gap between a canonically eligible task and its PR landing in `dev`.

```text
review_approved exact-head task OR active permitted merge_then_review task
-> clean task PR into dev -> immutable exact-head smoke -> synchronous REST merge
-> leave canonical task status unchanged for its downstream lifecycle
```

## Core Lifecycle & Authority Boundaries

1. **Canonical Truth Binding**:
   - Auto-integrator discovers candidates from the canonical status root
     (`PANTHEON_STATUS_ROOT` environment variable or `--status-file` option).
   - A review-before-merge task is eligible only at `review_approved`; an
     `in_progress`/`review` task is eligible only when its canonical contract
     explicitly permits `merge_then_review` with no independent reviewer.
     `ReviewGate` re-evaluates that row against the live PR before merge.
   - Local task worktree copies (`ROOT / ai-status.json`) are disposable execution projections
     and do not mask canonical runtime truth.

2. **Review Gate Enforced**:
   - Merge authority is governed by `scripts/git/task_review_merge_gate.py`.
   - For tasks requiring independent review, only the exact head approved by the assigned
     reviewer can be merged. The REST merge request carries that SHA as its
     optimistic-concurrency guard.
   - Standing auto-merge requests on gated PRs are actively revoked before evaluation.

3. **Owner-Finalize Handoff (No Unauthorized `done` Mutation)**:
   - The auto-integrator merges the PR into `dev` without changing canonical
     task status. A review-before-merge task remains `review_approved`; a
     merge-then-review task remains active for its post-merge review/finalize
     lifecycle.
   - The integrator **never** calls `scripts/ai_status.py done` or changes task status to `done`.
   - The supervisor detects that the task's PR is merged into `dev` and dispatches the task owner
     via `owned_finalize_dispatch` with an owner lease.
   - The owner then performs canonical closeout (review manifest binding, verification,
     evidence checks, delivery metadata, and `scripts/ai-status.sh done`).

4. **Idempotent Reruns on Merged PRs**:
   - If the task PR is already merged into `dev`, reruns report
     `already_merged` and preserve the task's canonical status without creating
     spurious unblock tasks.

## Safety Model

- **Dry-run by default**: `--execute` is required to mutate git/GitHub state.
- **Single-flight lock**: `.orchestrator/auto-integrator.lock` under
  `PANTHEON_STATUS_ROOT` uses a kernel `flock` plus PID/owner metadata. Process
  exit releases the kernel lock, dead legacy owners are recovered, and a live
  owner is never displaced. An overlapping scheduled pass exits successfully
  with `reason=integration_lock_held` (and the same machine-readable JSON
  reason under `--json`); corrupt or unusable lock state exits nonzero.
- **Dedicated writable clean checkout**: live `--execute` requires an explicit
  `coordination.repositories.<id>.integration_path`; `local_path` remains the
  worker/source checkout and is only a dry-run/test fallback. Promotion
  atomically installs versioned standalone clones for Pantheon and
  `execute_plans`. While holding the lock, the runner verifies the selected
  checkout and Git common dir are writable and the worktree is clean.
- **Sole task merge owner**: `task_finalize.sh`, `safe_pr.sh`, workers, and
  reviewers may push/open/revoke, but never merge or enable auto-merge. Only
  the scheduled canonical supervisor integration runner uses `--execute`; it
  calls the synchronous pull-request REST merge endpoint with the exact head
  and accepts only a response containing `merged: true`.
- **Fail-closed checks**: Draft PRs, missing PRs, CI failures, rebase conflicts, or head drift
  produce an `INTEGRATION-UNBLOCK-*` task to assign the blocker back to the owner/reviewer.
- **Environment-only recovery**: If a task was already exactly approved and was blocked solely
  because the integrator could not acquire its writable lock or Git worktree, local Human/Ops may
  run `ai-status.sh resume_integration <task-id> <message>`. It accepts only a blocked task whose
  pull-request delivery binding, review binding, and GitHub approval evidence still match. It does
  not approve or merge anything; the next integrator pass rechecks the live PR head and CI.

## CLI Usage

### Dry-run discovery and gate validation
```bash
python3 scripts/git/auto_integrator.py --json
```

### Test against an isolated status file
```bash
python3 scripts/git/auto_integrator.py --status-file /tmp/test-status.json --json --no-lock
```

`--no-lock` is dry-run test-only and is rejected together with `--execute`.

## Scheduled Runner

`scripts/run-auto-integrator.sh` is the supervisor-owned cron wrapper that
passes canonical status/config paths. `scripts/auto_integrator_install.py`
installs it every five minutes with tag `# pantheon-auto-integrator`; runtime
promotion repoints that entry. Workers and PR helpers do not invoke the
executing wrapper themselves.
