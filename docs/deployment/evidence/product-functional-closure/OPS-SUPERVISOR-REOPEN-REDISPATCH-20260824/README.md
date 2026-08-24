# OPS-SUPERVISOR-REOPEN-REDISPATCH-20260824 Evidence

## Overview

This task adds a bounded `review_reopen_revision` to the supervisor dispatch identity signature and candidate evaluation. When a reviewer rejects and reopens a task, the task returns to its unchanged owner for exactly one fresh dispatch without triggering an in-progress polling loop or requiring artificial task reassignments.

## Remediation

1. **Bounded Review-Reopen Revision in Dispatch Signature**:
   - Added `task_review_reopen_revision(task, activity_events=..., config=...)` in `.orchestrator/supervisor.py`.
   - `ready_dispatch_signature` includes `"review_reopen_revision"`.
   - `build_dispatch_event` projects `review_reopen_revision` into the event payload when > 0 and incorporates it into the dispatch event key (`dispatcher:{target_agent}:{task_id}:{reason}:{signature}`).

2. **Single Reopen Redispatch without Polling Loops**:
   - Reopening advances the revision (e.g. from 0 to 1), creating a unique event key that bypasses prior `seen_event_keys` entries.
   - Once dispatched, the key is recorded in `seen_event_keys`.
   - Subsequent supervisor polling ticks on the in-progress task generate the identical key, which is suppressed under `unchanged_cooldown`.

3. **Assignment Generation Preserved**:
   - Task `generation` remains untouched and continues to represent genuine assignment/reassignment boundaries.
   - No queue retry loops, watchdog bypasses, or manual task mutations were introduced.

4. **Source Ingestion and Runtime Boundaries**:
   - Changes are strictly isolated to `.orchestrator/supervisor.py` and its tests; no product runtime, deployment, or Source Ingestion changes.

## Verification

- `PYTHONPATH=.orchestrator:. python3 -m unittest discover -s .orchestrator -p test_supervisor.py` (121 tests passed, including reopen revision extraction, single redispatch, subsequent poll suppression, stale event rejection, and explain_dispatch trace).
- `PYTHONPATH=.orchestrator:. python3 -m unittest discover -s .orchestrator -p test_explain_dispatch.py` (7 tests passed).
