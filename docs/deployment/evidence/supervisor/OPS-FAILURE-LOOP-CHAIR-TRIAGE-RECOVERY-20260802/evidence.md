# OPS failure-loop chair triage and recovery

Task: `OPS-FAILURE-LOOP-CHAIR-TRIAGE-RECOVERY-20260802`

Owner: `Codex`

Reviewer: `Codex2`

Initial authoritative cutoff: `2026-08-02T12:52:52Z`

Resumed authoritative cutoff: `2026-08-02T13:25:09Z`

## Outcome

The resumed authoritative baseline contains 15 task-agent pairs at or above the
configured failure-loop reassignment threshold of 2. None satisfies all stale
`missing_process` recovery guards: every pair has active work, unresolved PR or
replacement execution, or a terminal/auth condition that is not eligible for
stale-process recovery. Consequently this task did not reassign, reopen, close,
clear, or otherwise mutate any of those 15 task rows.

One additional pair observed in the pre-recovery snapshot temporarily left the
threshold set through a canonical Human/Ops reassignment and supervisor
dispatch. Later canonical routing returned it to Codex and the supervisor
launched a fresh active Codex lease, so it is again one of the 15 pairs. The
other added pair is the pending Codex2 exact-head review for
`LIFECYCLE-PROJ-STORE-001`. This task recorded both transitions and did not
signal a worker or mutate either task row.

The machine-readable record in [evidence.json](./evidence.json) contains the
full task status, role, failure, PID generation, queue, worktree, branch, PR,
check, merge, dependency, and recovery-decision evidence for every pair.

## Authoritative state and safety boundary

- Canonical status root: `/home/lupin/pantheon`
- Governed command root: `/home/lupin/pantheon-ci-deploy/dev-root`
- Command runtime SHA: `941c15a34208e54e96cdd148ba3a5bfcd339abab`
- Source baseline: `origin/dev` at
  `0404ca01ebb6803df6a4b927bacada5739f61de1`
- Live supervisor: PID `3538768`, started `2026-08-01T15:47:45Z`
- Threshold: `2`; no threshold reset or failure-streak clear was performed
- No direct edit was made to canonical state, queue, activity, runtime, provider
  configuration, worker process, or service

The running command root predates the merged V3 record/normalizer changes and
the V2 decision/dispatch-consumption changes. The pure evaluator from
`origin/dev` therefore rejects every legacy live record with
`invalid_failure_record`. This is the intended fail-closed result: the evidence
does not promote/restart the runtime and does not treat a legacy streak as an
authorized recovery generation.

## Snapshot and classification

| Task-agent pair | Canonical state | Failure | PR/continuity evidence | Decision |
| --- | --- | --- | --- | --- |
| `L12-SIGNOFF-TASK-CARD-ASSIGNMENT-ALIGN-20260801:antigravity` | `todo`; Antigravity → Codex2 | `terminal`, 2 | #4450 open; trailer check failed; provider dispatch paused | Preserve terminal/auth blocker |
| `OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001:codex` | `in_progress`; Codex → Codex2 | `missing_process`, 2 | #4303 open/behind; unresolved replay bypass | Preserve unresolved implementation |
| `SUP-GOVERNANCE-HANDOFF-ACTIVE-LEASE-GUARD-OPERATOR-V8-20260802:codex` | `in_progress`; Codex → Codex2 | `generic_exit`, 2 | #4508 open/blocked; live PID/starttime-bound worker and queue lease | Preserve active execution |
| `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729:codex` | `in_progress`; Codex → Codex2 | `missing_process`, 2 | #4363 open/behind; prior #4333 merged | Preserve unresolved closeout refresh |
| `L12-FLEET-STATUS-SYNC-001:codex2` | `review_approved`; Codex2 → Codex | `missing_process`, 2 | #4297 open/behind; implementation #4282 merged | Preserve open closeout PR |
| `L12-FLEET-STATUS-SYNC-CLOSEOUT-20260728:codex2` | `todo`; Codex2 → Codex | `terminal`, 4 | #4313 open | Preserve terminal/open PR |
| `LIFECYCLE-PROJ-PLAN-COMPOSED-HEAD-REVIEW-20260801:codex2` | `review`; Codex → Codex2 | `missing_process`, 2 | #4466 open/behind; no eligible independent fallback | Preserve review blocker |
| `LIFECYCLE-PROJ-STORE-001:codex2` | `review`; Codex → Codex2 | `missing_process`, 2 | repaired #4503 head open/blocked with green Branch CI; exact-head review pending | Preserve pending review |
| `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729:codex2` | `review`; Antigravity → Codex2 | `missing_process`, 2 | #4389 open/dirty | Preserve conflicted PR |
| `SUP-L12-LONG-FINALIZE-LEASE-20260729:codex2` | `review`; Antigravity → Codex2 | `missing_process`, 2 | #4376 open/behind; trailer check failed | Preserve failing PR |
| `SUP-L12-MERGED-ROW-RECONCILE-20260729:codex2` | `review_approved`; Codex2 → Codex | `missing_process`, 2 | #4384 open/behind; prior #4379 merged | Preserve open closeout PR |
| `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731:codex2` | `todo`; Codex2 → Codex | `missing_process`, 4 | replacement V2 #4468 open/behind | Preserve replacement execution |
| `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731:codex2` | `review`; Antigravity → Codex2 | `missing_process`, 2 | #4468 open/behind; trailer check failed | Preserve failing replacement PR |
| `SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731:codex2` | `review_approved`; Codex2 → Codex | `missing_process`, 4 | #4396 open/behind | Preserve open ReviewBus PR |
| `SUP-L12-RUNNING-OWNER-RECONCILE-20260729:codex2` | `review_approved`; Codex2 → Codex | `terminal`, 2 | #4386 open/dirty | Preserve terminal/conflicted PR |

At the resumed cutoff, the V8 pair had one active queue record, one active task
worktree, and a live worker at PID `2027754` / start ticks `11383562`. No pair
had an archive duplicate. The declared dependencies were separately confirmed
archived `done`. The detailed JSON preserves the latest worker lease, recorded
PID/starttime readback or explicit unavailable value, queue, worktree, PR,
checks, merge state, dependency, and remote branch truth.

## Canonical recovery event

At `12:48:44Z`, the pre-recovery snapshot also contained
`SUP-GOVERNANCE-HANDOFF-ACTIVE-LEASE-GUARD-OPERATOR-V8-20260802:codex`.
Human/Ops assigned the task from Codex/Codex2 to Codex2/Codex at `12:49:24Z`.
The supervisor queued event `evt-20260802T124929Z-be244703` and started worker
`codex-20260802T124947Z-17a280cd` at `12:49:47Z`, with PID `1828128`, start
ticks `11203893`, and the expected per-task branch/worktree. Canonical readback
at `12:49:58Z` showed `in_progress`, owner Codex2, reviewer Codex. That explains
its absence from the initial 13-pair post-recovery baseline.

After later Human/Ops review-routing attempts, the running supervisor
helper-claimed the row to Codex while Codex2 was paused and started worker
`codex-20260802T131943Z-73ad13da` at `13:19:43Z`. The pair therefore re-entered
the resumed 15-pair set. The worker was live at the cutoff with child PID
`2027799` / start ticks `11383592`; this task did not signal it or perform that
concurrent reassignment.

## Capacity recomputation

At `12:54:47Z`, four workers were active globally and no separate pending-only
queue entry existed:

| Identity | Direct capacity | Active load | Nominal free | Independently dispatchable work |
| --- | ---: | ---: | ---: | --- |
| Codex | 4 | 2 | 2 | 0; the only two other direct candidates are chair-held failure-loop rows |
| Codex2 | 4 | 2 | 2 | 0; the other ten direct candidates are chair-held failure-loop rows |

There were no helper candidates. Threshold task IDs are excluded from helper
claims, and no other plan row passed helper eligibility. Therefore fewer than
four workers per identity is explained by eligibility, not hidden load or
capacity loss.

The resumed `13:25:09Z` readback showed three Codex workers and no Codex2
workers. Codex had one nominal free slot, but a `13:24:07Z` provider refresh
paused `codex1` after the CLI model cache failed schema decoding; its two other
direct rows also remained failure-loop/chair-held with unresolved PRs. Codex2
had four nominal slots but remained under the `13:11:48Z` authentication pause,
and all eleven direct candidates were unresolved threshold pairs. Neither
identity had an unrelated helper candidate. These exact blockers, rather than
an artificial dispatch, explain the resumed under-four load.

The active Codex2 reviewer for `LIFECYCLE-PROJ-STORE-001` was not terminated or
preempted to free capacity. That reviewer used a governed reopen with concrete
PostgreSQL findings; after its process disappeared, the supervisor—not this
task—performed the normal retry/re-dispatch transition. The active Codex V8
work was likewise not signalled or preempted.

## Mutation accounting

- Affected threshold-pair mutations by this task: `0`
- Failure-streak clears: `0`
- Threshold resets: `0`
- Process signals or service restarts: `0`
- Direct canonical-state edits: `0`
- This task's own governed mutations: the initial `progress` update under the
  former Codex2 lease, the reviewer `reopen`, and the supervisor's governed
  owner/reviewer rebind plus fresh Codex worker lease. No cross-task mutation
  was made by this Codex worker.

Zero affected-pair mutations is the recovery decision, not an omitted action:
no remaining pair passed the complete safe-stale predicate. Cross-task task
mutations also remain bound to the target task's governed lease and were not
attempted through this worker's lease.

## Validation and review

The resumed focused suite passed with 22 tests and 45 subtests (494 deselected),
the 15-pair jq acceptance contract passed, and the working-tree whitespace
check and `git diff --check origin/dev...HEAD` passed on the clean replacement
head. Commands and exact results are recorded in `evidence.json`. The final
exact-head review and merge identities are added during governed closeout.
