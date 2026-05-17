# Dispatch Policy Contract

Status: task-scoped contract for OPS-REFACTOR-001

This refactor re-applies the intent from tag `archive/codex-orchestrator-dispatch-policy-cleanup-2026-04-28` on top of current master instead of cherry-picking the archived supervisor change. The archived tag introduced the dispatch policy extraction, but current `supervisor.py` has since gained sidecar, disabled-agent, and orphaned-queue settings that must remain part of the policy defaults.

## Public Helpers

- `dispatch_reason_priority(reason)` returns the current execution dispatch order: review wakeups first, then owner finalize, owner in-progress, and owner ready work. Unknown reasons return `None`.
- `is_execution_dispatch_reason(reason)` recognizes only execution task wake reasons and excludes coordination or discussion-planning wakeups.
- `normalized_status_set(values, default)` lowercases configured status values and uses `default` only when `values is None`.
- `ready_dispatch_settings(config)` returns the `ready_dispatcher` settings with current supervisor defaults filled in.

## Supervisor Boundary

`supervisor.py` keeps its public API and queue/event behavior. It imports the helpers from `.orchestrator/dispatch_policy.py` and uses them at dispatch eligibility, status sync, stale-event, and priority checks. The refactor intentionally avoids changing status lifecycle rules or task assignment semantics.

## Current Defaults

The extracted policy preserves these current-master defaults:

- review statuses: `["review"]`
- finalize statuses: `["review_approved"]`
- owned statuses: `["in_progress", "todo"]`
- dependency done statuses: `["done"]`
- worker terminal statuses: `done_statuses` when configured, otherwise `["done", "review_approved"]`
- active worker statuses: `["running", "waiting_approval", "retry_backoff", "manual_pending", "stalled"]`
- sidecar-only agents: `[]`
- disabled agents: `[]`
- max tasks per agent: `1`
- max dispatches per tick: `4`
- orphaned queue event grace seconds: `300`

## Verification

Focused verification for this contract is:

```bash
cd .orchestrator
PYTHONPATH=. pytest -q test_dispatch_policy.py
PYTHONPATH=. pytest -q test_supervisor.py
```
