# SUP-PROVIDER-STALE-CACHE-READMISSION-PROBE-20260802 evidence

Task: Recover a freshly reauthenticated provider before stale capability
admission rejection

Implementation owner: Codex · Independent source reviewer: Codex2 ·
Post-merge closeout owner: Codex2 · Closeout reviewer/root gate: Human/Ops ·
Status: **source reviewed and merged; owner closeout prepared**

## Delivered contract

The source task closes the `auto_refresh_provider_capabilities=false` deadlock
observed on 2026-08-02. A persisted Codex2 `auth_ready=false` result could stop
`dispatch_ready_tasks()` before any delivery event existed, while the normal
fresh probe lived later in `process_queue()`. Freshly valid credentials could
therefore never reach their own recovery check.

`probe_provider_reports()` now performs a read-only stale-auth candidate
preflight before runtime admission. A candidate must still have a task that
passes the existing lifecycle, dependency, active/pending queue, capacity,
quota, failure-loop, chair-triage, and event-cooldown checks. The preflight
ignores only the cached provider-auth booleans that it is trying to verify; it
does not create a task assignment or queue event.

When a candidate exists, the supervisor probes at most one stale account per
cycle by default. Aliases and worker slots are de-duplicated through the
existing explicit provider account identity, while the configured provider key
is preserved for the actual CLI profile. The existing failed-probe interval is
checked before a forced live call, so a failed, ambiguous, or unsupported
attempt cannot create a per-tick probe herd.

Only `source=live` results are projected into the capability report and
persisted through `write_provider_capabilities()`. A fresh ready result restores
the healthy delivery fields. Revoked, quota, timeout, error, unsupported,
ambiguous, and raised-exception outcomes remain fail closed with their explicit
probe status. Cached results never transition the lane.

The normal `refresh_provider_auth_before_dispatch()` launch probe is unchanged:
the selected logical owner or concrete slot is still force-probed again just
before a worker starts.

## Scope boundaries

The task changes only provider probe due-state exposure, supervisor pre-lock
target selection/projection, regression tests, and this evidence directory.
The rebuilt source task branch has no task-brief history. The supervisor may
materialize an untracked worker-context brief for a later wake-up, but that
file remains outside git and outside the reviewed source scope. The task does
not change
credentials, Codex/Codex2 account
or quota separation, canonical task truth, reviewer eligibility, worker limits,
priority, dependency, failure-loop, chair-triage, service state, deployment, or
product code.

## Dependency and merge order

`SUP-PROVIDER-PAUSE-RECOVERY-PROBE-20260802` is archived `done`; its completion
merge `16135f2316541591e54cba6d58f0a80273731bc1` and implementation merge
`71fea47cbe65bb3e07792afa7e9f0134fd0066f5` are ancestors of this task branch.

This source task must merge before
`SUP-AUTOWORKER-QUOTA-ROUTING-LIVE-CANARY-OPERATOR-V9-20260802`. No live supervisor
mutation or restart belongs to this task.

## Verification

- Checkout-scoped Python provisioning passed.
- Focused stale-cache and pause recovery slice: 11 tests and 5 subtests passed.
- Provider-permissions regression: 83 tests and 7 subtests passed in 6.71
  seconds.
- Full supervisor regression: 523 tests and 152 subtests passed in 65.71
  seconds.
- `py_compile` and `git diff --check` passed.
- Evidence JSON validation is required again on the final task head.

The post-merge owner closeout rerun passed the same focused slice with 11 tests
and 5 subtests, provider permissions with 83 tests and 7 subtests, and the full
supervisor suite with 523 tests and 152 subtests. The closeout also re-ran
`py_compile`, evidence JSON parsing/assertions, and `git diff --check`.

The first repo-root pytest attempt was intentionally discarded as validation
evidence because `.orchestrator/test_supervisor.py` imports `supervisor` as a
top-level module. The authoritative runs used the repository's established
`.orchestrator/` working directory with the provisioned interpreter.

## Review, delivery, and owner closeout

Codex2 independently approved PR #4510 exact head
`ea358fa7c2bfc8e5b9042736d5776cf9421c2ab0` at
`2026-08-02T13:36:38Z`. The review re-ran focused 11 tests plus 5 subtests,
provider 83 tests plus 7 subtests, and supervisor 523 tests plus 152 subtests;
it also checked `py_compile`, JSON, diff, trailers, dependency ancestry, the
five-file source scope, empty task-brief history, and successful GitHub checks.

Human/Ops supplied the protected exact-head merge gate at
`2026-08-02T13:37:45Z`. GitHub merged PR #4510 into `dev` as
`71654443b53ba32f66e046aa7dcb12e85127edb2` at
`2026-08-02T13:37:53Z`; the reviewed head remains an ancestor of current
`origin/dev`.

The canonical post-merge assignment is owner Codex2 and reviewer Human/Ops.
This closeout update records only the immutable source review and merge facts.
It does not change source, provider accounts, quota groups, reviewer policy,
credentials, runtime state, or rollout authority. The governed `done`
transition remains withheld until this evidence-only closeout commit is
reviewed and merged to `dev`.

Rollback is a merge revert. That restores the prior fail-closed stale-cache
behavior without changing credentials or canonical task state.
