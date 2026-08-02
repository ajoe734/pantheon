# SUP-PROVIDER-STALE-CACHE-READMISSION-PROBE-20260802 evidence

Task: Recover a freshly reauthenticated provider before stale capability
admission rejection

Owner: Codex · Reviewer: Codex2 · Status: **ready for independent exact-head
review**

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
The rebuilt task branch has no task-brief history. It does not change
credentials, Codex/Codex2 account
or quota separation, canonical task truth, reviewer eligibility, worker limits,
priority, dependency, failure-loop, chair-triage, service state, deployment, or
product code.

## Dependency and merge order

`SUP-PROVIDER-PAUSE-RECOVERY-PROBE-20260802` is archived `done`; its completion
merge `16135f2316541591e54cba6d58f0a80273731bc1` and implementation merge
`71fea47cbe65bb3e07792afa7e9f0134fd0066f5` are ancestors of this task branch.

This source task must merge before
`SUP-PROVIDER-STALE-CACHE-READMISSION-LIVE-CANARY-20260802`. No live supervisor
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

The first repo-root pytest attempt was intentionally discarded as validation
evidence because `.orchestrator/test_supervisor.py` imports `supervisor` as a
top-level module. The authoritative runs used the repository's established
`.orchestrator/` working directory with the provisioned interpreter.

## Review and rollback

Codex2 must independently review the exact PR head, re-run the focused and full
relevant checks, confirm the five-file declared source/test/evidence scope and
empty task-brief history, and replace the pending review fields in
`evidence.json` with the real decision before final owner closeout. No review
decision is claimed in this owner-authored packet.

Rollback is a merge revert. That restores the prior fail-closed stale-cache
behavior without changing credentials or canonical task state.
