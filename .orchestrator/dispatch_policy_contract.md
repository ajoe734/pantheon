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

There is no file-inbox/manual-pending fallback, chair lane, discussion-planning
lane, helper claim, priority preemption, or direct retry launch.

## Verification

Focused verification for this contract is:

```bash
cd .orchestrator
PYTHONPATH=. pytest -q test_dispatch_policy.py
PYTHONPATH=. pytest -q test_supervisor.py
```
