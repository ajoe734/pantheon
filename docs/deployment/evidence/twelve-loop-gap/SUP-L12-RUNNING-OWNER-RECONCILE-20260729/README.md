# SUP-L12 running owner reconciliation evidence

Task: `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`
Owner: Claude
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

## Killing a live worker requires canonical authorization

Marking a record superseded and terminating a running process are two different
acts. Reconciliation does the first from its own runtime observation, but the
second is governed by the same guard the `poll_workers` supersede path uses:

- `reconcile_worker_task_assignments` loads
  `worker_governance_activity_snapshot(config)` lazily — only when a losing
  worker is actually alive — and passes it to
  `active_worker_governance_lease_decision`;
- if the decision is not `terminate`, the process is preserved, one
  deduplicated `record_worker_governance_lease_guard` observation is written,
  and the supersede is skipped entirely;
- if `terminate_worker_process_generation` fails, the guard is recorded with
  the same `authorized_transition_termination_pending_confirmation` /
  `authorized_transition_process_identity_unproven` reason codes `poll_workers`
  uses;
- the chosen `reason_code` and `source_event_id` are carried into both the
  `assignment_reconciliation` evidence and the `worker_assignment_reconciled`
  audit event.

Only terminal task truth or the latest *exact* supervisor `task_reassigned`
event can preempt a running worker. A preserved worker is not hidden: it stays
active and unmatched, so it is still counted in `active_drift_count` and the
report status stays `drift`.

## Live row/run observation

Read from governed `ai-status show`, the exact central runtime record selected
by this worker's `ORCH_RUN_ID`, and the supervisor state worker entry for the
same run.

| Observed | Row owner | Row reviewer | Row status | Run id | Queue event | Worker / runner status | PID | Exit | Command source SHA |
|---|---|---|---|---|---|---|---:|---:|---|
| `2026-08-06T17:02:46Z` | Claude | Antigravity | in_progress | `claude1-4-20260806T165326Z-7ac2c9d2` | `evt-20260806T165308Z-aeb32134` | running / running | 278141 | null | `f90e0aae6cb5e86f18b20db9f30bc834f6115745` |
| `2026-08-06T14:42:30Z` | Antigravity | Claude | in_progress | `claude1-4-20260806T131930Z-35c86123` | `evt-20260806T131924Z-05f82088` | running / running | 3131300 | null | `f90e0aae6cb5e86f18b20db9f30bc834f6115745` |
| `2026-07-29T15:22:01Z` | Codex | Antigravity | in_progress | `codex-20260729T150602Z-743e6017` | `evt-20260729T150450Z-97c245cd` | running / running | 1671740 | null | `c1e396495d37a1c9dfeea5704e7eb73db6acde0e` |

The first row is the current cycle; the other two are retained under
`superseded_live_observations` in `evidence.json`. All three match their
authoritative assignment, so none is drift. They are recorded because
assembling this join is exactly the manual work the task removes: today the
row, the runner record, and the supervisor worker entry live in three separate
files.

Neither `/home/lupin/pantheon/.orchestrator/supervisor.py` nor the leased
command root `/home/lupin/pantheon-ci-deploy/dev-root` at
`f90e0aae6cb5e86f18b20db9f30bc834f6115745` contains
`task_assignment_at_dispatch` or `worker_assignment_reconciliation` (`grep -c`
returned 0 for both files at 2026-08-06T17:02:46Z), and the deployed
`state.json` carries neither key. The gap is still open in production; this PR
is what closes it.

## Regression evidence table

| Case | Authoritative row | Worker run evidence | Governance evidence | Decision |
|---|---|---|---|---|
| Active owner drift, canonical reassignment present | Claude2 / Antigravity / in_progress | `codex2-stale-live`; queue `evt-codex2-stale-live`; PID 4242; exit null; source `555…555` | exact supervisor `task_reassigned` (Codex2 → Claude2) after the worker's lease start | Terminate and supersede exactly once (`exact_owner_reassignment`); do not edit row |
| Active owner drift, no canonical reassignment evidence | Claude2 / Antigravity / in_progress | `codex2-unauthorized-live`; PID 4242; exit null; source `555…555` | empty governance activity tail | Preserve the process (`governance_only_transition`); one deduplicated `worker_governance_lease_preserved` event; report status `drift` |
| Recent terminal fallback | Claude2 / Antigravity / in_progress | `codex2-terminal`; PID 5151; exit 1; source `555…555` | n/a (not alive) | Retain/report terminal evidence; do not edit row |
| Duplicate active worker | Claude2 / Antigravity / in_progress | `claude2-incumbent` at 11:00Z and `claude2-duplicate` at 11:30Z | n/a (neither alive) | Keep incumbent; supersede duplicate exactly once |
| Stale failure write race | observed Codex2 owner; locked row Claude2 owner | `codex2-terminal-fallback`; PID 4242; exit 1; source `555…555` | n/a | Reject write and record `authoritative_assignment_changed` |

The full machine-readable rows are in `evidence.json`.

## Validation

Verified on implementation head `7d36e07bff93d0d08e3682c3b14f18294eeb5fc1`.
This note is part of an evidence-only commit on top of that head and changes no
implementation file, so the numbers below stay true of the PR head.

- Reconciliation suite: 13 passed (12 from `67496cd60` plus Pin D).
- Related worker/reassignment/ownerless suites: 135 passed, 4 subtests passed.
- Full supervisor suite: 619 passed, 162 subtests passed in 75.70s.
- Python compilation and `git diff --check`: passed.
- `.orchestrator/config.json`: unchanged relative to `origin/dev`.

Interpreter: `/home/lupin/pantheon/.venv/bin/python` (CPython 3.12.3). An
earlier revision of this note named a `.venv-pantheon` interpreter inside the
task worktree; no such directory exists there, and the path is corrected here
rather than silently dropped.

Raw command/results are archived in `validation.txt`.

## Ownership reassignment (2026-08-06)

The supervisor reassigned this task four times on 2026-08-06: Codex2 →
Antigravity at 10:19:20Z, Antigravity → Claude at 13:19:34Z, Claude →
Antigravity at 14:13:02Z, and Antigravity → Claude at 16:46:03Z after repeated
Antigravity provider timeouts. The canonical row now reads owner `Claude`,
reviewer `Antigravity`.

Commits `791e320a3`, `67496cd60` and `70dc78240` were authored under the
previous pair and still carry `LLM-Agent: Antigravity` / `Reviewer: Claude`
trailers. That is not a defect: `scripts/git/check_commit_trailers.py` validates
that each commit carries `LLM-Agent`, `Task-ID` and `Reviewer`, not that they
agree with the current canonical row. Commits authored from `7d36e07bf` onward
carry the current pair.

Because of the reassignments and because the task head advanced past the
previously approved commit, the 2026-07-29 approval no longer binds. It is kept
for audit under `superseded_reviews` in `evidence.json`:

| Superseded approval | Reviewer | Bound head | Approved at | Canonical event |
|---|---|---|---|---|
| `review_approved` | Antigravity | `665e4bdbd7a741ba2b808cee5302b764ba5ca597` | 2026-07-29T15:42:35Z | 5776 |

Owner actions in the current cycle (owner `Claude`, from 16:46:03Z):

- re-verified the two closed reopen sub-items against the pushed head:
  `worker_matches_current_assignment` is byte-identical to `origin/dev` (the
  three-dot diff adds only new symbols after it), and
  `reconcile_worker_task_assignments` calls
  `worker_dispatch_assignment_changed` rather than the shared helper;
- found the third sub-item still open in `67496cd60`: the
  `active_worker_governance_lease_decision` call passed `activity_events=None`
  (which makes the decision unconditionally
  `preserve` / `governance_only_transition`), discarded the result via
  `_ = lease_decision` under a comment that falsely claimed it was used in
  evidence logging, and terminated the live process regardless;
- implemented the real guard in `7d36e07bf` and pushed it to PR
  [#4386](https://github.com/ajoe734/pantheon/pull/4386);
- updated the two alive-worker supersede tests to supply the exact supervisor
  `task_reassigned` event and assert the recorded reason code, and added Pin D
  (`test_live_drift_without_canonical_authorization_is_preserved`);
- rebound this note, `evidence.json`, `validation.txt` and the task brief from
  the previous pair to the current pair, and refreshed the live row/run
  observation from this owner run.

Owner actions inherited from earlier cycles (retained for audit):

- merged `origin/dev` forward into
  `task/SUP-L12-RUNNING-OWNER-RECONCILE-20260729` to clear the `DIRTY` merge
  state on PR #4386; the only conflict was in
  `.orchestrator/test_supervisor.py`, where both branches appended independent
  test classes and both were kept;
- restored `worker_matches_current_assignment` and added
  `worker_dispatch_assignment_changed`, with pinning tests A and C
  (`67496cd60`).

## Independent review

Pending. A fresh independent review of implementation head `7d36e07bf` is
required under the current owner/reviewer pair (owner `Claude`, reviewer
`Antigravity`); `evidence.json` records `review.decision` as `pending`. No
approval has been issued for any head on this cycle.

## Governed status plane (recovered)

An earlier revision of this note recorded the governed status plane as
`fail_closed` on `activity_audit_integrity`, from a duplicate activity
`event_id`
`supervisor-reassign-6d984db0aafd0fe690ad2e9a0877bc8aa31b03e32aafa0b652f3c58ccb5af2da`
sealed into
`archive/logs/ai-activity-log.jsonl-d234b0ec08ec543209fcf989b4c6fff7fe3ebd46cf269e1ea4b17b2fc3768e2d.gz`.
That is no longer true and the record is superseded here rather than deleted.

Re-checked at `2026-08-06T16:54Z` against command root
`/home/lupin/pantheon-ci-deploy/dev-root` at source SHA
`f90e0aae6cb5e86f18b20db9f30bc834f6115745`:
`"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show` returns the canonical row
(`source: active`, owner `Claude`, reviewer `Antigravity`, status
`in_progress`). Status writes are available again. No Human/Ops escalation is
outstanding for this task.

## Boundaries

This task does not change provider configuration, preferred-lane order,
ownerless merged-delivery evidence, product services, or canonical task-state
files. The generated dashboard bundle and runtime lock files visible in the
worktree are supervisor-owned derived state and are intentionally excluded
from this task's commits.
