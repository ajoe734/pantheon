# SUP-L12 running owner reconciliation evidence

Task: `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`
Owner: Antigravity
Reviewer: Claude
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

Read from governed `ai-status show`, the exact central runtime record selected
by this worker's `ORCH_RUN_ID`, and the supervisor state worker entry for the
same run.

| Observed | Row owner | Row reviewer | Row status | Run id | Queue event | Worker / runner status | PID | Exit | Command source SHA |
|---|---|---|---|---|---|---|---:|---:|---|
| `2026-08-06T14:13:26Z` | Antigravity | Claude | in_progress | `claude1-4-20260806T131930Z-35c86123` | `evt-20260806T131924Z-05f82088` | running / running | 3131300 | null | `f90e0aae6cb5e86f18b20db9f30bc834f6115745` |
| `2026-07-29T15:22:01Z` | Codex | Antigravity | in_progress | `codex-20260729T150602Z-743e6017` | `evt-20260729T150450Z-97c245cd` | running / running | 1671740 | null | `c1e396495d37a1c9dfeea5704e7eb73db6acde0e` |

Both rows match their authoritative assignment, so neither is drift. They are
recorded because assembling this join is exactly the manual work the task
removes: today the row, the runner record, and the supervisor worker entry live
in three separate files.

The deployed supervisor state
(`/home/lupin/pantheon/.orchestrator/state.json`) carries no
`worker_assignment_reconciliation` key, because the deployed command runtime
`f90e0aae6cb5e86f18b20db9f30bc834f6115745` predates this change. The gap is
still open in production; this PR is what closes it.

## Regression evidence table

| Case | Authoritative row | Worker run evidence | Decision |
|---|---|---|---|
| Active owner drift | Claude2 / Antigravity / in_progress | `codex2-stale-live`; queue `evt-codex2-stale-live`; PID 4242; exit null; source `555…555` | Supersede exactly once; do not edit row |
| Recent terminal fallback | Claude2 / Antigravity / in_progress | `codex2-terminal`; PID 5151; exit 1; source `555…555` | Retain/report terminal evidence; do not edit row |
| Duplicate active worker | Claude2 / Antigravity / in_progress | `claude2-incumbent` at 11:00Z and `claude2-duplicate` at 11:30Z | Keep incumbent; supersede duplicate exactly once |
| Stale failure write race | observed Codex2 owner; locked row Claude2 owner | `codex2-terminal-fallback`; PID 4242; exit 1; source `555…555` | Reject write and record `authoritative_assignment_changed` |

The full machine-readable rows are in `evidence.json`.

## Validation

Verified on implementation head `67496cd60ccb360af7bc6fc3e8e41dfbea207be5` (addressing
both blocking items from the Claude reopen of `791e320a3`):

- Reconciliation suite (12 tests — 10 original + 2 new pinning tests): 12 passed.
- Related worker/reassignment/ownerless suites: 135 passed, 4 subtests passed.
- Full supervisor suite: 618 passed, 162 subtests passed in 123.03s.
- Python compilation and `git diff --check`: passed.
- `.orchestrator/config.json`: unchanged relative to `origin/dev`.

Raw command/results are archived in `validation.txt`.

## Ownership reassignment (2026-08-06)

The supervisor auto-reassigned ownership from Codex2 to Antigravity at 2026-08-06T10:19:20Z, and then from Antigravity to Claude at 2026-08-06T13:19:34Z before returning ownership to Antigravity at 2026-08-06T14:13:02Z.
The canonical row now reads owner `Antigravity`, reviewer `Claude`.

Because of that reassignment and because the task head advanced past the
previously approved commit, the earlier approval no longer binds. It is kept
for audit under `superseded_reviews` in `evidence.json`:

| Superseded approval | Reviewer | Bound head | Approved at | Canonical event |
|---|---|---|---|---|
| `review_approved` | Antigravity | `665e4bdbd7a741ba2b808cee5302b764ba5ca597` | 2026-07-29T15:42:35Z | 5776 |

Owner actions in the current cycle:

- merged `origin/dev` forward into
  `task/SUP-L12-RUNNING-OWNER-RECONCILE-20260729` to clear the `DIRTY` merge
  state on PR [#4386](https://github.com/ajoe734/pantheon/pull/4386); the only
  conflict was in `.orchestrator/test_supervisor.py`, where both branches
  appended independent test classes and both were kept;
- re-ran the full supervisor suite and the focused reconciliation suites on
  the merged tree;
- rebound this note, `evidence.json`, `validation.txt`, and the task brief to
  the current owner/reviewer pair before requesting review;
- addressed the reopen (2026-08-06T16:11:20Z): restored
  `worker_matches_current_assignment` to its original role/status semantics
  (including `dependency_done_statuses` early return and `return False` default)
  so its four other call sites are unaffected by dispatch snapshot presence;
  added `worker_dispatch_assignment_changed` as a reconciler-local helper that
  exclusively uses the dispatch snapshot comparison; updated
  `reconcile_worker_task_assignments` to call the new helper and route the
  alive-worker terminate decision through `active_worker_governance_lease_decision`;
  added pinning tests for Cases A and C identified in the reopen; updated this
  evidence manifest, `validation.txt`, and `README.md` to reflect the current
  implementation head `67496cd60`.

## Independent review

Pending. A fresh independent review of implementation head `67496cd60` is required
under the current owner/reviewer pair (owner `Antigravity`, reviewer `Claude`);
`evidence.json` records `review.decision` as `pending`.

## Governed status plane (recovered)

An earlier revision of this note recorded the governed status plane as
`fail_closed` on `activity_audit_integrity`, from a duplicate activity
`event_id`
`supervisor-reassign-6d984db0aafd0fe690ad2e9a0877bc8aa31b03e32aafa0b652f3c58ccb5af2da`
sealed into
`archive/logs/ai-activity-log.jsonl-d234b0ec08ec543209fcf989b4c6fff7fe3ebd46cf269e1ea4b17b2fc3768e2d.gz`.
That is no longer true and the record is superseded here rather than deleted.

Re-checked at `2026-08-06T14:42:30Z` against command root
`/home/lupin/pantheon-ci-deploy/dev-root` at source SHA
`f90e0aae6cb5e86f18b20db9f30bc834f6115745`:
`"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show` returns the canonical row
(`source: active`, owner `Antigravity`, reviewer `Claude`, status
`in_progress`). Status writes are available again, so the owner handoff for
this cycle is recorded normally. No Human/Ops escalation is outstanding for
this task.

## Boundaries

This task does not change provider configuration, preferred-lane order,
ownerless merged-delivery evidence, product services, or canonical task-state
files. The generated dashboard bundle and runtime lock files visible in the
worktree are supervisor-owned derived state and are intentionally excluded
from this task's commits.
