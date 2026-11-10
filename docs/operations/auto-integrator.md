# Auto Integrator Operations Guide

Status: active operations guide for `scripts/git/auto_integrator.py`.
Last updated: 2026-11-09

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

2. **Review Gate & Policy Alignment Enforced**:
   - Merge authority is governed by `scripts/git/task_review_merge_gate.py` with canonical TaskStore exact-head review as the mandatory merge prerequisite.
   - For tasks requiring independent review, only the exact head approved by the assigned
     reviewer can be merged. The REST merge request carries that SHA as its
     optimistic-concurrency guard.
   - Standing auto-merge requests on gated PRs are actively revoked before evaluation.
   - **Review Bridge & Status Check Policy Alignment**:
     - **Live mode (`review_gate.github_review_bridge_required: true`, `review_gate.github_review_identity: "shared"`, live since runtime `30ae728a8` on `2026-11-08T12:50Z`)**: `.orchestrator/config.json` declares `"Pantheon canonical review gate"` in `branch_workflow.task_pr.required_status_checks`, and GitHub branch protection requires `Pantheon canonical review gate` (bound to GitHub Actions `app_id: 15368`) on both `ajoe734/pantheon` `dev` (5 required checks: `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, `Service suites`, `Pantheon canonical review gate`; `strict: false`, no `required_pull_request_reviews`, `enforce_admins: true`, gate added `2026-11-09T13:26:52Z`) and `ajoe734/execute-plans` `dev` (4 required checks: `Commit trailers`, `Generated files guard`, `Component merge gate`, `Pantheon canonical review gate`; `strict: true`, `required_pull_request_reviews` with `0` approvals, `enforce_admins: false`, gate added `2026-11-09T23:36:52Z`). `master` (`pantheon`, 3 checks) and `main` (`execute-plans`, 3 checks) do not require `Pantheon canonical review gate` because promote pull requests do not run the gate workflow.
     - Under shared identity mode, `scripts/git/github_review_bridge.py` records `approve` and `reopen` decisions as a `COMMENT` pull request review (`event: "COMMENT"`, matched state `"COMMENTED"`) carrying the review marker on the exact PR head, pushes the `refs/tags/pantheon-review/<decision>/<head_sha>` proof tag, and dispatches `.github/workflows/canonical-review-gate.yml` (`github-actions[bot]`) as the sole issuer of the `Pantheon canonical review gate` status.
     - **Self-heal for missing approve proof**: When `review_bridge_is_required` is `true`, the `Pantheon canonical review gate` check is not green, no `approve`, `operator-accept`, or `reopen` proof tag exists yet for the exact head, and the active TaskStore holds a `review_approved` for that exact head by an assigned reviewer distinct from the owner, `auto_integrator.py` self-heals by publishing the approve proof through `bridge_review_decision` with that reviewer as actor, dispatching the workflow, and reporting `"waiting"` instead of opening a `ci-red` unblock task. It never publishes a proof without an exact-head approval by a reviewer distinct from the owner and never publishes when a `reopen` tag exists. When a valid `approve` or `operator-accept` proof tag already exists (and `reopen` is confirmed absent) while the gate check is not yet green, `auto_integrator.py` re-dispatches `canonical-review-gate.yml` and waits.
     - Failures or pending states on any declared check (`Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, `Service suites`, `Pantheon canonical review gate`) block merge fail-closed without a REST merge or integration receipt, and GitHub itself refuses a merge into either `dev` branch without a green `Pantheon canonical review gate` posted by the GitHub Actions workflow. Operator-directed direct deliveries therefore require a `task/<TASK-ID>` branch with an operator acceptance proof tag (`refs/tags/pantheon-review/operator-accept/<head_sha>`). Residual risk: a holder of the shared `ajoe734` GitHub credential can still deliberately push a proof tag or add the `delivery:tooling` label directly; only a separate review identity (`review_gate.github_review_identity: "independent"`) closes that.
     - **Documented rollback mode (`review_gate.github_review_bridge_required: false`, TaskStore-only rollback only)**: To roll back, first remove `Pantheon canonical review gate` from GitHub branch protection in **both** repositories (`ajoe734/pantheon` `dev` and `ajoe734/execute-plans` `dev`) **before** setting `review_gate.github_review_bridge_required` to `false` (and removing `"Pantheon canonical review gate"` from `branch_workflow.task_pr.required_status_checks`) and promoting the runtime, because the verifier (`scripts/git/canonical_review_gate_ci.py`) stops publishing once the flag is `false` and every merge into `dev` would then be blocked. Only when rolled back to that false mode must `Pantheon canonical review gate` be absent from `branch_workflow.task_pr.required_status_checks`, and external canonical review gate failures are then treated as non-blocking diagnostics (`Ignored explicitly non-required diagnostics: Pantheon canonical review gate`).
     - Contradictory, missing, or malformed review bridge configuration fails closed immediately during discovery, evaluation, and final pre-merge revalidation.
       Final revalidation reports `contradictory-review-bridge-policy`, a shared repair-producing reason: execute mode publishes a durable unblock request for supervisor materialization after releasing the authority locks. This configuration repair does not use the authority-only `final-review-contract-changed` reason, which intentionally suppresses recursive review repair tasks.

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
- **Fail-closed checks**: Draft PRs, missing PRs, CI failures, contradictory review bridge policy, rebase conflicts, or head drift
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

## Recovery Verification

After changing the scheduled runtime, publish a task-scoped redacted evidence
manifest. Keep the operator-owned host assertion separate from checks that an
isolated worker can reproduce. At minimum, record:

- the immutable command-runtime SHA and the live config's matching Pantheon
  `integration_path`;
- the single tagged cron assertion and two serialized execute-tick outcomes;
- the exact PR head, merge commit, and canonical `integration_receipt` for any
  merge performed during recovery;
- merged ancestry and archived `done` state for all recovery targets; and
- an explicit check that an overlapping tick reports
  `integration_lock_held`, rather than starting a second merge path.

Do not copy credentials, environment contents, full process command lines, or
unredacted host configuration into repository evidence. A worker that cannot
read the host crontab must label the single-entry binding as Human/Ops-attested
and independently verify the runtime paths, receipts, ancestry, and lock log.

## BEHIND return-to-owner routing

An approved (review-before-merge), green PR whose fresh `mergeStateStatus` is
`BEHIND` and whose disposable trial merge of the target branch is clean
(`exact_head_verified_clean`) used to stay `waiting` forever. It is now routed
through the existing content-addressed unblock inbox with dedicated reason
`exact-head-behind-base` (`requires_source_reopen` is `True`;
`requires_repair_task` is `False`):

- At most one BEHIND return per repository and target branch may be outstanding
  at a time: the oldest eligible candidate by approval time (task id breaks
  ties; never dict or row order) is returned, and no further BEHIND candidate in
  that repository/target branch is returned while a processed
  `exact-head-behind-base` receipt names a source task that is still in the
  canonical `tasks` list with status `in_progress`, `review`, or
  `review_approved` at a generation at least the returned one (or while an
  unprocessed `exact-head-behind-base` request is still in the inbox). `done`
  tasks leave `tasks`, and `blocked` or `todo` source tasks do not hold the
  queue so one stuck task cannot freeze the repository. Other BEHIND candidates
  stay `waiting` and report which task they are queued behind.
- Under the canonical task-state lock, the supervisor's
  `materialize_auto_integrator_unblock_requests` revalidates that the source task
  (repository identity compared through the canonical resolver
  `task_target_repository_id`, so `target_repo: execute-plans` matches the
  config-key `repository_id: execute_plans`; a missing `target_repo` means
  `pantheon` and an unknown one is rejected) is still `review_approved` with the same `generation` and `delivery_binding`
  PR/head as the request. If stale, it records a rejected receipt and changes
  nothing. If valid, after releasing the canonical lock it invokes
  `scripts/ai_status.py reopen <source_task_id> <message>` through
  `status_command_subprocess_context(config)` with `AI_NAME=Human/Ops`,
  `PANTHEON_LOCAL_HUMAN_OPS=1`, and
  `HUMAN_OPS_REASON=auto-integrator-behind-unblock:<request_sha256>`
  (`ORCH_RUN_ID` stripped)—without creating a child repair task—with a fixed
  message telling the owner to merge `origin/<target>` into the task branch with
  no other change and resubmit, and telling the reviewer to confirm
  `git diff origin/<target>...HEAD` equals the previously approved change. On a
  non-zero exit code it keeps the request in the inbox for retry; on success it
  writes the `processed` receipt. Replays are idempotent by revalidating that
  the source is still `review_approved` at the request head before calling.
- Repair tasks created from an unblock request carry a delivery rule in their
  acceptance and `next`: the fix is delivered from the repair task's own branch
  and PR (its own Task-ID only) and the source PR is superseded or no longer
  stranded. The worker must not push to the source task's branch, because
  commits carrying the repair task id make the source task's handoff fail the
  commit trailer check (`check_commit_trailers`, unchanged). If the defect is in
  the source PR's own commits, the repair branch starts from `origin/dev` and
  restates the needed change as its own commit. A needed file outside the repair
  task's artifact contract is reported as a block naming the file, not worked
  around. The exact-head-behind-base source return is unchanged.
- Only `BEHIND` routes. Other non-direct merge states keep waiting; a real
  conflict keeps the `exact-head-merge-conflict` path.
- The integrator never pushes or rewrites the approved head.
- The one-level repair rule still applies: a repair task that is itself BEHIND is
  excluded from routing and reported as waiting for an operator.
- Branch protection and the strict requirement are unchanged. Takes effect only
  after a runtime promotion.

## BLOCKED with in-flight checks waits

When `mergeStateStatus` is `BLOCKED`, the integrator first scans the raw
`statusCheckRollup` (not `latest_check_runs`) for any entry whose conclusion,
state or status is in `PENDING_VALUES`. If one exists it returns `waiting`,
names the incomplete checks in the detail, and opens no `merge-state-blocked`
unblock task. A same-name run superseded by a later-started one (for example
the duplicate push/pull_request `Branch CI Gate` runs) can still be running
while GitHub reports `BLOCKED`. When every rollup entry is complete, `BLOCKED`
still opens the `merge-state-blocked` task. Other merge states are unchanged.
