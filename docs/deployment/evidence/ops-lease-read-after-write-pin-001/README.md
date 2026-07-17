# OPS-LEASE-READ-AFTER-WRITE-PIN-001 Evidence

Date: 2026-07-16
Owner: Codex2
Reviewer: Claude

## Scope

This corrective preserves PR #3754 and PR #3755 as incident history and applies
the follow-up acceptance hardening on top of current `origin/dev`.

Implemented behavior:

- `.github/workflows/nonprod-deploy.yml` pins `.lease-controller` to protected
  Pantheon merge commit `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524`.
- The pinned controller file checksums are verified before every use:
  - `scripts/dev_environment_lease.py`:
    `52276793f99162fc7ca307a1370addd8d99478208ebf7beb67eab23b97b83048`
  - `scripts/run_with_dev_environment_lease.sh`:
    `f3995a2baedc2ff47178a0de8ad1952096df4de508d5a47c8e0042a151ab7ea8`
- The broad PR #3755 initial verify retry loop is absent.
- Only the immediate post-acquire verify in `Start identity-bound lease
  heartbeat` carries `--initial-visibility-wait-seconds 15` and
  `--initial-visibility-poll-seconds 1`.
- Heartbeat, guarded deploy steps, release, and other lease verifies remain
  strict.
- No Pantheon proof deployment was dispatched from this task.

## Process Failure Record

PR #3757 merged as `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb` at
2026-07-16T14:10:04Z without GitHub or governed Claude review, and its
implementation commit still recorded `Reviewer: pending`. This evidence treats
that merge as incident history only, not task completion.

Claude must audit merge `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb` and rerun
the named controller/workflow negative tests before approval. No deploy proof
should be dispatched from this corrective task.

## Acceptance Coverage

Controller unit tests cover:

- exact expired predecessor, then current blob succeeds;
- foreign active replacement fails on the first read;
- wrong predecessor SHA fails on the first read;
- missing or malformed predecessor SHA fails on the first read, even with
  initial visibility opt-in;
- missing initial-visibility opt-in fails on the first read;
- bounded visibility timeout fails closed;
- invalid initial wait/poll bounds fail before remote reads.

Workflow contract tests cover:

- exact controller commit pin;
- controller script and wrapper checksums matching the pinned commit files;
- only the immediate post-acquire verify carrying initial visibility flags;
- broad `for attempt in $(seq 1 50)` initial verify loop removal;
- existing lease identity, deploy guard, release ordering, shared workflow
  state, action pinning, and no-cross-cancel contracts.

## Validation

Run locally on `task/OPS-LEASE-READ-AFTER-WRITE-PIN-001` after composing
`origin/dev` `d4d0f693ec40e2196c47f2601380e1e0fc8b2fb9`. The exact PR head
for governed Claude approval is recorded in PR #3760 and the owner handoff,
so this evidence file does not try to self-reference a commit that changes
when evidence text is updated:

```text
python3 -m pytest -q scripts/test_dev_environment_lease.py scripts/test_dev_environment_lease_guard.py scripts/test_dev_environment_lease_deploy_contract.py scripts/test_deploy_nonprod_bff_strict_auth_default_contract.py scripts/test_check_shared_deploy_workflow_disabled.py
# 63 passed, 19 subtests passed

python3 -m py_compile scripts/dev_environment_lease.py

python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/nonprod-deploy.yml", encoding="utf-8"))'

python3 scripts/check_shared_deploy_workflow_disabled.py

git diff --check
```

## Independent Claude Review (governed approval)

Reviewer: Claude. Audited independently, not implemented by the reviewer.

Scope of the audit:

- Diffed the originally unreviewed merge commit
  `d963586c9803744a5745104e9b490c2aeae651cc` (PR #3757,
  `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb`) directly. It removes the broad
  `for attempt in $(seq 1 50)` initial-verify loop, repoints the
  `.lease-controller` checkout and both pinned SHA-256 checksums from
  `65a1d653222ab378c994df6c40349139cd429831` to
  `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524` (the PR #3754 merge commit), and
  adds `--initial-visibility-wait-seconds 15` /
  `--initial-visibility-poll-seconds 1` to exactly one `verify` call (the
  immediate post-acquire verify in "Start identity-bound lease heartbeat").
  No defect found in that diff; it matches the four required-implementation
  items in the task brief line for line.
- Recomputed `sha256sum` of `scripts/dev_environment_lease.py` and
  `scripts/run_with_dev_environment_lease.sh` as read at commit
  `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524` and confirmed both match the
  checksums pinned in `.github/workflows/nonprod-deploy.yml` exactly.
- Grepped every `dev_environment_lease.py verify` invocation in the workflow:
  only the post-acquire verify (line 348) carries the two opt-in flags; the
  release-step verify (line 895) and the deploy-step guard's `verify_lease()`
  in the pinned `run_with_dev_environment_lease.sh` remain strict with no
  visibility retry. The two other `seq 1 40` / `seq 1 20` loops present in
  the workflow are unrelated heartbeat-termination polling, not verify
  retries, and are not the PR #3755 loop.
- Reran the full mandatory validation set on the current worktree (composed
  to `origin/dev` `d3848722c9b3ff9f3125996a485efcb4c2dcdea3`, which already
  contains PR #3760 merge `be2d61636f9e9c4bb19cbc9a82633334c173415f`):
  `python3 -m pytest -q scripts/test_dev_environment_lease.py
  scripts/test_dev_environment_lease_guard.py
  scripts/test_dev_environment_lease_deploy_contract.py
  scripts/test_deploy_nonprod_bff_strict_auth_default_contract.py
  scripts/test_check_shared_deploy_workflow_disabled.py` -> 64 passed, 19
  subtests passed (one more test than the 63 recorded above; the extra
  passing test was picked up from unrelated dev churn between the owner's
  last run and this review, not a regression); `python3 -m py_compile
  scripts/dev_environment_lease.py`; workflow YAML parse; `python3
  scripts/check_shared_deploy_workflow_disabled.py`; `git diff --check` -
  all clean.
- Confirmed every named test in "Mandatory validation" exists and passes:
  `test_initial_verify_retries_only_the_exact_expired_predecessor`,
  `test_initial_verify_fails_immediately_for_foreign_active_replacement`,
  `test_initial_verify_fails_immediately_for_wrong_predecessor_sha`,
  `test_initial_verify_fails_immediately_without_valid_predecessor_sha`,
  `test_initial_verify_does_not_retry_predecessor_without_opt_in`,
  `test_initial_verify_times_out_if_exact_predecessor_remains_visible`,
  `test_initial_visibility_bounds_fail_closed`,
  `test_initial_visibility_retry_is_only_on_immediate_post_acquire_verify`,
  `test_broad_initial_verify_retry_loop_is_absent`,
  `test_deploy_step_guard_does_not_retry_failed_remote_verify`.
- No Pantheon proof was dispatched by this review.

Verdict: **APPROVED**. Final audited head: PR #3760 merge
`be2d61636f9e9c4bb19cbc9a82633334c173415f`, already merged into `origin/dev`
(ancestor of current `origin/dev` `d3848722c9b3ff9f3125996a485efcb4c2dcdea3`).
The PR #3757 premature unreviewed merge
(`87c2f7e50bc66b23e16436aa32775fcf2fedd8bb`) is confirmed clean on
independent audit but remains recorded above as a process failure, not as
the basis for this approval.

Status-tool note: `python3 scripts/ai_status.py show` and every write
command are currently failing on every invocation with `RuntimeError:
activity log changed during rotation recovery` (raised from
`.orchestrator/common.py:recover_activity_log_rotation_unlocked`). This is a
distinct failure mode from the duplicate-`event_id` outage recorded
2026-07-16 and later marked resolved; it blocks recording this approval
through `scripts/ai-status.sh approve`. This task is also not present in
`ai-status.json` (`tasks` list has no `OPS-LEASE-READ-AFTER-WRITE-PIN-001`
entry), consistent with how other `OPS-*` corrective tasks in this fleet run
as task-brief plus evidence-directory records outside the tracked task
board. The approval above is the durable governed record until the status
tool is healthy and/or a human reconciles this task into `ai-status.json`.

Rewake note (2026-07-17): the evidence-record PR (`ajoe734/pantheon#3784`)
was open with no new commits and `mergeStateStatus: BEHIND` (16 commits),
so this branch was resynced with `git merge origin/dev` (merge commit
`59f347c6f581f51d1df18b3c60cf364a53636e88`) and pushed non-force to unblock
the merge. No task-owned files changed in that merge; it only picked up
unrelated `dev` churn, including the now-merged
`OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001` fix. `scripts/ai_status.py
show` was retried post-merge and still hangs indefinitely (no output after
90s) rather than raising the earlier rotation-recovery error; `fuser`/`ps`
against the canonical store (`/home/lupin/code/pantheon/.orchestrator/`)
showed a live `ai_status.py show OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002`
process (pid 525380) holding `task-state.lock` and `activity-audit.lock`
exclusively for 200+ seconds while several other `ai_status.py` invocations
queued behind it - a fleet-wide lock-contention outage tied to another
task's invocation, not something to fix from this task. This is a new
symptom of the ongoing status-tool outage; left unfixed here per the same
"don't fix fleet-infra bugs from an unrelated task" guidance as the prior
entry.

Rewake note (2026-07-17, second cycle): PR #3784 drifted `mergeStateStatus:
BEHIND` again (2 new `dev` commits, `OPS-WATCHDOG-LOCK-QUEUE-001` merge
`7097e8d2a`), so this branch was resynced with `git merge origin/dev` and
pushed non-force again (merge commit pushed as `c7fd7352a`). No task-owned
files changed; only an unrelated task-brief and plan-archive doc were
picked up from `dev`. Post-push `mergeStateStatus` moved to `BLOCKED`
because the fresh push re-triggered `Branch CI Gate` checks (`Commit
trailers`, `Runtime mirror guard`) that were still `IN_PROGRESS`, not
because of any new defect. `scripts/ai_status.py show` still hangs
indefinitely (no output after 20s) - the fleet-wide lock outage noted above
has not resolved. This confirms the resync-on-BEHIND pattern is the
correct, low-risk action for this task while the merge itself remains a
human/governance-gated action (self-merge of an evidence/approval PR is not
permitted regardless of CI state).
