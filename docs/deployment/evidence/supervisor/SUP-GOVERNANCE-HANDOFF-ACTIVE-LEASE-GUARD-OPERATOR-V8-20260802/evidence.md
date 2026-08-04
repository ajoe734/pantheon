# SUP-GOVERNANCE-HANDOFF-ACTIVE-LEASE-GUARD-OPERATOR-V8-20260802 evidence

Owner: Codex2 · Canonical reviewer: Human/Ops · Status: **changes requested
on prior head; corrected head pending re-review**

## Result

Healthy workers no longer lose their active execution lease merely because the
task moved through assign, note, reopen, review, approval, or handoff governance.
The supervisor records that observation and lets the worker finish naturally.
Exact reassignment, explicit cancel/supersession, terminal task truth, and the
existing eligible priority-preemption path can still terminate work, but only
after the worker's full task/run/queue/PID/starttime process generation is
validated.

Approval resume is now covered by the same contract. When a resumed Claude
process replaces the suspended worker PID, the supervisor reads the new Linux
starttime, recomputes the task/run/queue/PID/starttime generation, and publishes
that refreshed identity in both worker state and the `worker_resumed` audit.

`ai_status.py` now attaches the same exact worker-lease identity to canonical
status-command audit events. This makes the execution producer, reviewer
provenance, canonical lifecycle transition, and supervisor lease decision
separately inspectable without copying runtime-only fields into the task row.

## Governed owner/reviewer provenance

The task began with owner Codex and reviewer Human/Ops. Human/Ops approved old
head `7b268b468ca9` at `13:10:20Z`, then revoked that decision at `13:12:14Z`
after reviewer fallback drift. A task-scoped Codex2 review rejected that same
head at `13:38:55Z` for the stale resume generation and inconsistent evidence
provenance. After corrective PR #4515 merged, Human/Ops governed assignment
event `ai-status-event-23fe9f2a...` bound the current owner/reviewer pair as
Codex2/Human/Ops, and Human/Ops reopened the task at `14:12:05Z` with the exact
correction list recorded in `evidence.json`.

Codex2 is only the task-scoped owner/quota fallback. Codex and Codex2 remain
distinct configured account and quota identities; no reviewer, account, quota,
or provider policy is changed by this task.

## Incident reproduction

The task used only the two scoped runner records and their matching worker logs;
it did not scan the full activity history.

| Run | Governance event | Runner outcome |
|---|---|---|
| `codex-20260802T093212Z-e5e0963b` | V4 owner handoff at `09:45:14Z` | SIGTERM / exit 143 at `09:45:36Z` |
| `codex-20260802T093612Z-0686f7cb` | runtime-promotion owner handoff at `09:49:22Z` | SIGTERM / exit 143 at `09:50:17Z` |

The runner-status SHA-256 values and matching command-root worker-log hashes are
recorded in `evidence.json`. Before this repair,
`poll_worker_assignment_stage()` treated the canonical handoff/review outcome
or assignment mismatch as authority to call `terminate_worker_pid()` and then
publish completion/supersession. That conflated governance lifecycle with the
healthy process lease.

## Delivered contract

- A deterministic process-generation digest binds schema version, task ID,
  worker run ID, queue event ID, PID, and Linux `/proc` starttime ticks.
- Signal delivery requires both the stored generation and a current starttime
  match. An unreadable process or reused PID is never signaled.
- The recent validated canonical audit tail is consumed in append order. The
  latest relevant event after lease acquisition controls reassignment evidence.
- Ordinary governance events preserve the lease. Missing, invalid, stale, or
  concurrently divergent evidence also preserves it and records why.
- Only a valid supervisor `task_reassigned` event that actually moves this
  worker's dispatched role can authorize reassignment termination.
- Canonical `done`, `cancelled`/`canceled`, or superseded truth may end the
  lease, including the archived-task case where only the valid terminal audit
  event remains.
- Eligible priority preemption remains supported and is upgraded from bare PID
  signaling to exact process-generation termination.
- Under runtime admission, termination is deferred until lock release. The
  worker stays nonterminal until identity-bound confirmation runs outside the
  lock, and the deferred decision has its own audit event.
- A resumed worker replaces all three mutable process fields together: PID,
  PID starttime, and process-generation digest. Both supervisor identity
  validation and ai-status command-lease validation cover the replacement.

## Scope boundary

This task changes only the supervisor and ai-status lease/audit surfaces, their
tests, and this evidence directory. It does not modify account or quota groups,
configured identity equivalence, reviewer policy, provider configuration, live
runtime/services, deployment, product tasks, or canonical JSON by hand. Codex
and Codex2 remain distinct configured identities and quota groups, with no new
mutual-review restriction.

## Owner verification

| Command | Result |
|---|---|
| Focused supervisor active-lease matrix | 13 passed, 513 deselected, 2 subtests |
| Focused ai-status lease/root matrix | 10 passed, 148 deselected, 2 subtests |
| Clean exact ai-status lease test | 1 passed |
| Resume generation supervisor regression | 1 passed |
| Resumed ai-status lease regression | 1 passed |
| Full supervisor suite after PR #4515 rebase | 543 passed, 154 subtests |
| Full ai-status suite | 159 passed, 31 subtests |
| Python compile | passed |
| Evidence JSON parse | passed |
| `git diff --check` | passed |
| Task-brief history | empty in `origin/dev..HEAD` |

Commit-trailer range validation and GitHub exact-head CI are required after the
final evidence commit and before governed handoff.

## Review, rollout, and rollback

Human/Ops' current decision is changes requested against prior head
`7b268b468ca9`; the decision at `14:12:05Z` requires the #4515 rebase, resume
generation refresh/regression, corrected provenance, and empty generated-task-
brief history. Those corrections are now implemented and must be handed off for
a new independent Human/Ops exact-head decision. The governed handoff and
approval bind GitHub `headRefOid`, since this committed manifest cannot
self-reference its containing commit. Source merge does not authorize a live
rollout. A later governed rollout must replace or drain workers launched by the
older runtime schema. Rollback is a revert of the eventual task merge commit;
no state, configuration, or data migration exists.
