# Task Brief: OPS-DEPLOY-WORKFLOW-GUARD-001

> Temporary coordination routing: until
> `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-001` is accepted, every owner or
> reviewer working this task must run governed state, review, handoff and
> closeout commands through `/home/lupin/code/pantheon/scripts/ai-status.sh`
> with its own real identity (`AI_NAME=Codex2` for the owner or
> `AI_NAME=Codex` for the reviewer).
> Do not use the task-worktree wrapper for state. Git and tests stay in the
> task worktree. Verify with central `show`.

## STOP gate after unreviewed PRs #3753 and #3755

Do not enable auto-merge and do not dispatch another Pantheon proof yet.

- PR #3753 merged without reviewer acceptance and can still lose concurrent
  records or overwrite malformed data. Fleet task
  `OPS-RECONCILIATION-JSON-STORE-INTEGRITY-001` owns that corrective. Wait for
  its exact-head Antigravity approval and reviewed merge.
- PR #3755 also merged before reviewer acceptance and conflicts with the safer
  selective controller delivered by PR #3754. Fleet task
  `OPS-LEASE-READ-AFTER-WRITE-PIN-001` must remove the broad YAML loop, pin the
  exact selective controller and checksum, and prove exact-predecessor-only
  behavior with executable tests.
- PR #3757 later merged the selective pin as
  `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb`, again without its assigned
  Claude review. It remains unaccepted until Claude independently audits the
  exact merge and records the result. Its existence does not open this STOP
  gate.
- Both merged PRs are incident evidence only. Neither is accepted completion
  evidence for this task.
- After both correctives are accepted, rerun Pantheon only. The task remains
  incomplete until that run reaches terminal success with both previously
  unhealthy services healthy and all hosted probes executed successfully.

## Problem

Workers protect their own deploy runs by **disabling the shared GitHub workflow and cancelling other people's runs**. This freezes the deploy path for the whole fleet, including tasks that are strictly upstream of the worker doing the freezing.

This is a coordination defect, not a bug in any single task. Several tasks already recorded it in their own blockers without anyone connecting them.

## Observed evidence (2026-07-16, Human/Ops session)

### 1. The `LOOP-PROD-FE-001` proof guard (worst instance, now paused)

A worker spawned this loop (captured live from `/proc/<pid>/cmdline`):

```bash
end=$((SECONDS+2400)); allow_deploy=29466971724; allow_branch='task/PINT-010-R2-MOBILE-PROOF'
while (( SECONDS < end )); do
  pstate=$(gh api repos/ajoe734/pantheon/actions/workflows/269991390 --jq .state)
  estate=$(gh api repos/ajoe734/execute-plans/actions/workflows/292028803 --jq .state)
  if [ "$pstate" = active ]; then gh workflow disable 269991390 --repo ajoe734/pantheon; fi
  if [ "$estate" = active ]; then gh workflow disable 292028803 --repo ajoe734/execute-plans; fi
  for status in in_progress queued; do
    # cancel every pantheon deploy run except $allow_deploy
    # cancel every execute-plans deploy run, with no exception at all
    # cancel every sibling integration run not on $allow_branch
  done
  sleep 10
done
```

Three distinct defects in this one loop:

- **It disables workflows shared by the whole fleet** to protect one run.
- **It cancels runs it does not own.** It killed `EVOCHAIN-011` run 29467384238 — a task that is upstream of the guard's own owner.
- **It does not exit when its protected run finishes.** The captured instance started 02:36:33 and was scheduled to run until ~03:16, but its protected run 29466971724 reached `success` at **02:38:54**. It spent 30+ minutes cancelling deploys to protect a completed run.

### 2. The pattern is NOT unique to that task

Workflow 269991390 was disabled again at **03:41:57**, when:
- the `LOOP-PROD-FE-001` worker was already killed (no process),
- no `allow_deploy` guard process existed anywhere on the box,
- a `publish/v2026.07.15.2` run had started 36s earlier at 03:41:21.

So a different worker did a one-shot "dispatch mine, then lock the door behind me". Any fix must address the pattern, not delete one loop.

### 3. Already-recorded blockers this explains

- `EVOLOOP-009`: "latest-dev strict deploy runs 29465456181 and 29465639316 were cancelled by a respawned local proof guard that cancels Pantheon Nonprod Deploy. Clear or retarget the guard, then rerun..."
- `LOOP-PROD-TEL-002`: "workflow 269991390 is disabled_manually, so next strict dev/root probe rerun cannot be dispatched."

### 4. Cost

Deploy run outcomes by branch over the last 40 runs:

| branch | total | success | failure | cancelled |
|---|---|---|---|---|
| dev | 22 | 2 | 6 | **14** |
| task/EVOCHAIN-011 | 6 | 0 | 0 | **5** |
| publish/v2026.07.15.2 | 7 | 5 | 0 | 1 |

The cancellations are the guard, not real failures.

## Scope

Make it impossible for one task's proof run to freeze the fleet's deploy path, while still giving a proof run the isolation it legitimately needs.

The workflow **already has a concurrency group** ([.github/workflows/nonprod-deploy.yml:70](../../.github/workflows/nonprod-deploy.yml#L70)):

```yaml
concurrency:
  group: pantheon-nonprod-deploy-${{ github.event_name == 'push' && (...) || inputs.environment }}
  cancel-in-progress: ${{ github.event_name == 'push' }}
```

That is the sanctioned isolation mechanism. A proof run needing exclusivity should use it (or a dedicated group), not global disable + cancel.

**Do not** simply delete the guard from one task's playbook. The disable/cancel behavior appears in more than one worker, so the fix has to be a rule the fleet cannot casually break, plus a check that catches it.

## Acceptance

- No worker playbook, skill, or task brief instructs a worker to run `gh workflow disable` / `gh run cancel` against a workflow or run it does not own. Grep the repo and the skills for the pattern and remove every instance.
- A documented, enforced rule states that shared CI/CD workflows are fleet infrastructure: a task may not disable them, and may not cancel runs it did not dispatch.
- Proof runs that need exclusivity obtain it through the workflow concurrency group (or a dedicated group / an explicit lease), with the mechanism documented.
- Any remaining guard-like helper must exit as soon as its protected run reaches a terminal state, and must be scoped to runs it dispatched.
- A guard/check detects a shared deploy workflow left in `disabled_manually` and reports it, so this cannot silently freeze the fleet again. Consider whether `scripts/reap_hung_workers.py` + the existing cron pattern is the right home.
- Evidence: show `nonprod-deploy` staying `active` across a proof run, and a non-owner deploy run completing while a proof run holds its own isolation.

## State when this was written

- `LOOP-PROD-FE-001` is **blocked / waiting_for Human/Ops** with the guard defects in its blocker, and is pinned in `.orchestrator/auto-unblock-state.json` at `reopens: 2` so auto-unblock leaves it for a human. Unpin it only once the guard is fixed.
- Dev BFF auth secrets are provisioned; the strict auth floor now passes (run 29467232569 `Enforce dev auth deployment floor` = success, vs run 29422676364 = failure before). The deploy path is otherwise clear.
- Separate, unrelated failure still open: strict dev/root deploy run 29469469807 reached the VM ssh step and failed with `gcloud.compute.ssh ... Unable to acquire impersonated credentials`. Not branch-conditioned (dev has 2 historical successes). Out of scope here; do not conflate.

## Constraints

有疑問一定要提出,不要自己亂做。若設計稿沒寫到、與既有 code 對不上、或無法重現,一律 STOP 並用 blocker 寫清楚問題等待澄清,不要臆測或繞過。不要為了讓自己的 proof 通過而重新引入任何形式的全域 disable/cancel。

## Current planner authorization and remaining acceptance (2026-07-16)

Owner Codex2 is authorized to cancel or force-cancel **only** stale queued
Pantheon run `29469158508` (`task/EVOCHAIN-011`, queued since
`2026-07-16T03:31:02Z`). Capture its API state before and after and record
the exact command. Do not cancel, force-cancel, disable, or otherwise mutate
any other Pantheon or execute-plans run/workflow.

After the one allowed cleanup:

1. dispatch task-owned Pantheon and execute-plans dev deploy proofs;
2. prove both shared workflows remain `active` while the runs coexist;
3. wait for both runs to reach terminal `success`;
4. record run IDs, commit SHAs, timestamps, concurrency/lease evidence and
   zero cross-cancellation;
5. submit follow-up evidence through a PR for reviewer Codex.

PR #3740 is interim evidence only. Its formal planner review explicitly
states that it cannot close this task.

## Reviewer evidence correction (2026-07-16 12:55Z)

Run `29469158508` is a GitHub ghost run: both exact run-scoped cancel APIs
returned HTTP 500 and it remains queued. Preserve that packet as incident
evidence, but do **not** claim it blocks every new Pantheon deploy. The same
workflow produced terminal runs after the ghost was queued, including
`29469560985` (`success`) and `29498360289` (`failure`).

Therefore the remaining acceptance is still executable: dispatch fresh,
task-owned Pantheon and execute-plans dev proof runs, show that they coexist
without either workflow being disabled or another run being cancelled, and
wait for both to reach `success`. A failed proof run must be diagnosed and
rerun; the ghost run alone is not a completion blocker.

## First coexistence proof attempt (2026-07-16 13:03Z)

The owner dispatched both task-owned proofs at the same second without any
cross-cancel or workflow disable:

- execute-plans run `29500642299`, SHA
  `62a9bb03b17037b40153ccb1a150974cf188779b`, completed `success` at
  `2026-07-16T13:18:32Z`;
- Pantheon run `29500642280`, SHA
  `cd842177612c3f92ca0c92cb42e522431dc70b75`, completed `failure` in
  `Deploy dev VM stack under lease`.

The Pantheon failure is a real deployment failure, not a concurrency or
cross-cancellation failure. `pantheon-reconciliation-drift-svc-1` remained
unhealthy, which also left `pantheon-loop-run-projector-scheduler-1`
unhealthy; the deploy exited 75 and every hosted probe was skipped. The run
log did not print the failing reconciliation service's application logs, so
the evidence is not yet sufficient to identify root cause.

Codex2 must preserve this attempt as failed evidence, collect the exact
reconciliation healthcheck/application failure from the VM or an improved
task-owned diagnostic run, make or dispatch any required repair through its
own reviewed task/PR, and rerun the Pantheon proof. Do not rerun
execute-plans merely to replace its valid success; the final evidence must
show that the successful execute-plans run overlapped the failed attempt and
that a later Pantheon rerun succeeds with both workflows still active and no
cross-cancel.
