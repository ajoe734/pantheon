# SUP-L12 running owner reconciliation evidence

Task: `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`
Owner: Codex
Reviewer: Antigravity
Review manifest: `evidence.json`

## Outcome

The supervisor now joins canonical task assignment truth to both active and
terminal worker records. The join reports owner, reviewer, task status, run id,
queue event, PID, exit code, worker status, dispatch identity, and the
supervisor-issued command source SHA.

The task row remains authoritative:

- an active worker for an earlier owner/reviewer is superseded and its queue
  lease is settled;
- multiple active workers for the same authoritative task retain the oldest
  matching incumbent and supersede later duplicates;
- delegated fallback parent/child records are not treated as accidental
  duplicates;
- a recently terminal mismatched run remains available as evidence but cannot
  rewrite the task row;
- a failure reassignment that loses an owner/reviewer race is rejected inside
  the canonical lock and emits
  `stale_worker_failure_reassignment_skipped` with the before/after rows.

Runtime summary output now uses `assignment_truth=unknown` until the
reconciliation check has run. Verbose output prints every joined field before
the supervisor may report `assignment_truth=healthy`.

## Live row/run observation

Read at `2026-07-29T15:22:01Z` from governed `ai-status show` and the exact
central runtime record selected by this worker's `ORCH_RUN_ID`.

| Row owner | Row reviewer | Row status | Run id | Queue event | Worker / runner status | PID | Exit | Command source SHA |
|---|---|---|---|---|---|---:|---:|---|
| Codex | Antigravity | in_progress | `codex-20260729T150602Z-743e6017` | `evt-20260729T150450Z-97c245cd` | running / running | 1671740 | null | `c1e396495d37a1c9dfeea5704e7eb73db6acde0e` |

The materialized task brief recorded `todo` with the same owner/reviewer. That
is the expected pre-start snapshot; the governed successful-dispatch status
sync advanced the canonical row to `in_progress`.

## Regression evidence table

| Case | Authoritative row | Worker run evidence | Decision |
|---|---|---|---|
| Active owner drift | Claude2 / Antigravity / in_progress | `codex2-stale-live`; queue `evt-codex2-stale-live`; PID 4242; exit null; source `555…555` | Supersede exactly once; do not edit row |
| Recent terminal fallback | Claude2 / Antigravity / in_progress | `codex2-terminal`; PID 5151; exit 1; source `555…555` | Retain/report terminal evidence; do not edit row |
| Duplicate active worker | Claude2 / Antigravity / in_progress | `claude2-incumbent` at 11:00Z and `claude2-duplicate` at 11:30Z | Keep incumbent; supersede duplicate exactly once |
| Stale failure write race | observed Codex2 owner; locked row Claude2 owner | `codex2-terminal-fallback`; PID 4242; exit 1; source `555…555` | Reject write and record `authoritative_assignment_changed` |

The full machine-readable rows are in `evidence.json`.

## Validation

- New reconciliation suite: 7 passed.
- Related worker/reassignment/ownerless suites: 113 passed, 2 subtests passed.
- Full supervisor suite: 464 passed, 4 subtests passed.
- Python compilation and `git diff --check`: passed.
- `.orchestrator/config.json`: unchanged.

Raw command/results are archived in `validation.txt`.

## Independent review

Antigravity approved the implementation and evidence manifest at
`2026-07-29T15:42:35Z`, bound to PR
[#4386](https://github.com/ajoe734/pantheon/pull/4386) exact head
`665e4bdbd7a741ba2b808cee5302b764ba5ca597`. The review verified the
464-test supervisor result, worker owner drift reconciliation, duplicate
active-worker suppression, and the optimistic stale-failure reassignment
guard. The canonical approval is authoritative event sequence `5776`.

## Boundaries

This task does not change provider configuration, preferred-lane order,
ownerless merged-delivery evidence, product services, or canonical task-state
files. The generated dashboard bundle and runtime lock files visible in the
worktree are supervisor-owned derived state and are intentionally excluded
from this task's commits.
