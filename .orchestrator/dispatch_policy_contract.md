# Dispatch Policy Contract

Status: subordinate contract for Supervisor Authority V2

The authoritative architecture is
`docs/02-architecture/supervisor-authority-v2.md`. This module contains the
shared pure dispatch constants consumed by the planner and diagnostic CLI; it
does not define another scheduler.

## Public Helpers

- `dispatch_reason_priority(reason)` returns the current execution dispatch order: review wakeups first, then owner finalize, owner in-progress, and owner ready work. Unknown reasons return `None`.
- `is_execution_dispatch_reason(reason)` recognizes only execution task wake reasons and excludes coordination or discussion-planning wakeups.
- `normalized_status_set(values, default)` lowercases configured status values and uses `default` only when `values is None`.
- `ready_dispatch_settings(config)` returns the `ready_dispatcher` settings with current supervisor defaults filled in.

## Supervisor Boundary

`supervisor.py` imports these helpers for eligibility, delivery revalidation,
status synchronization, and stale-intent checks. Unknown reasons fail closed.

## Current Defaults

The extracted policy preserves these current-master defaults:

- review statuses: `["review"]`
- finalize statuses: `["review_approved"]`
- owned statuses: `["in_progress", "todo"]`
- dependency done statuses: `["done"]`
- worker terminal statuses: `["done", "review_approved"]`
- active worker statuses: `["running", "waiting_approval", "retry_backoff", "stalled"]`
- sidecar-only agents: `[]`
- per-agent capacity: required `agents.<id>.max_parallel`
- per-account capacity: `ready_dispatcher.max_concurrent_per_account`
- max dispatches per tick: `4`
- orphaned queue event grace seconds: `300`

## Completion tracks

String entries in `depends_on` remain terminal dependencies. A task may opt an
entry into `functional` or `hosted` completion through `dependency_tracks`.
Those tracks are satisfied only by an explicit `completion_tracks.<track>`
record with status `done`; terminal status is never inferred as functional
success. This lets local paper/replay work proceed while a hosted
`operator-live/write-proof` remains an external wait. The shared dev lease is
still required by the hosted controller and is not bypassed by this feature.

There is no file-inbox/manual-pending fallback, chair lane, discussion-planning
lane, helper claim, priority preemption, or direct retry launch.

## Execution authorization (OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001)

`evaluate_task_delivery_admission` also computes
`execution_authorization.is_execution_authorized(task, now=...)` and feeds it
into `TaskIntent.execution_authorized`. A privileged (`security`/`hosted`/
`live`) task whose `execution_authorization` subrecord is not currently
`STATE_GRANTED` and current is denied with
`DispatchBlockReason.EXECUTION_AUTHORIZATION_REQUIRED`, before any capacity,
health, or endpoint check. A non-privileged task, or any task with no
`execution_authorization` subrecord, is unaffected (`execution_authorized`
defaults to `True`). See `.orchestrator/execution_authorization.py`'s module
docstring and
`docs/04/pantheon_first_release_closure_2026-09-06/EXECUTION_AUTHORIZATION_SA_SD.md`
for the full policy/grant/one-shot-consume contract.

## Verification

Focused verification for this contract is:

```bash
cd .orchestrator
PYTHONPATH=. pytest -q test_dispatch_policy.py
PYTHONPATH=. pytest -q test_supervisor.py
```
