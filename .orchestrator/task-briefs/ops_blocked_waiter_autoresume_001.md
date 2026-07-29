# OPS-BLOCKED-WAITER-AUTORESUME-001 — auto-resume a blocked task when its waiter responds

## Problem

When a task is `blocked` with `waiting_for` set to an external party (most often
`Human/Ops`), and that party then responds — a `Human/Ops` note that resolves
the blocker, a merged decision/verdict PR, or an explicit unblock signal — there
is **no mechanism that returns the task to the owner's queue**. The task stays
`blocked` indefinitely because:

- a `blocked` task is not an owned-dispatch candidate, so the owner worker is
  never dispatched;
- `reopen`/`start` in `scripts/ai_status.py` require the actor to BE the owner
  or reviewer, so only a dispatched owner/reviewer worker could self-unblock —
  which never happens while it is blocked;
- the chair reassignment/triage path declines (`deny_sidecars`) because it
  correctly sees "needs direct owner resume", but has no action that performs
  that resume.

Observed twice on 2026-07-23/24: TJ-E2E-012 and PPL-ALLOC-009 both required
manual `runtime_state_lock` surgery (flip `.tasks.<id>.status` blocked ->
in_progress, drop `waiting_for`, clear the stale `seen_event_keys` dispatch-dedup
entries) before the fleet would resume them. That manual step is the only exit
today, and it is operator-only because runtime-state writes are gated.

### Hard repro — PPL-ALLOC-009 (2026-07-24), resists ALL external state edits

After the Human/Ops B1 decision merged (PR #4041), PPL-ALLOC-009 could NOT be
resumed by any external means. Four independent methods were tried and all
failed:

1. `ai_status.py reopen` (owner Codex) -> board flips to in_progress, then the
   supervisor reverts the WHOLE task record (status, `waiting_for`, `next`) back
   to a stale 2026-07-23 snapshot within one cycle, with no activity event.
2. `runtime_state_lock` flip of `.tasks.PPL-ALLOC-009.status` + dedup clear ->
   same revert.
3. A 30-minute guard loop re-applying both every 30s -> board flip-flopped
   in_progress/blocked ~15 times, never dispatched a worker.
4. Full supervisor restart (SIGTERM -> watchdog relaunch, fresh pid) with the
   board pre-set to in_progress -> the FRESH supervisor boot-read in_progress,
   held it ~2 min, then deliberately re-blocked it and kept it blocked.

Method 4 rules out a stale in-memory cache / write-race: a brand-new process
reading a good board still re-blocks this specific task. Dependencies were
verified satisfied (`dependencies_satisfied -> True`, all 9 deps `done`), all
blocker rows are `resolved`, owner Codex had free capacity, and the task is not
a human_gate by flag. So the re-block is driven by a persistent canonical source
the supervisor reconciles from that still marks PPL-ALLOC-009
blocked/`waiting_for: Human/Ops` — NOT by the working board or runtime `.tasks`
that operator edits can reach. TJ-E2E-012 escaped only because a governed worker
was actually dispatched and drove a canonical `review_approved` event; PPL never
got that far, so it is stuck.

Implication for this task: the fix MUST live in the supervisor's canonical
reconciliation, and it must produce a governed state transition (the same path a
dispatched worker's lease uses), because external board/runtime writes are
reverted. Identify the canonical store the boot/cycle reconcile reads for
`mutates_canonical: true` tasks and make "waiter satisfied" clear the blocked
mark THERE.

## Owned layer

`.orchestrator/supervisor.py` blocked-task reconciliation only. Add a bounded
"waiter satisfied -> resume" transition:

- Detect a `blocked` task whose `waiting_for` party has produced a resolving
  signal since `last_update`. Acceptable signals (start with the safe subset):
  1. an open blocker row for the task marked resolved, or
  2. a governed `note`/`handoff` authored by the `waiting_for` identity (e.g.
     `Human/Ops`) that references the task after it entered `blocked`.
- On detection, transition the task to `todo` (fresh owned dispatch) OR
  `in_progress`, clear `waiting_for`, resolve the open blocker row, and clear the
  stale `seen_event_keys` dispatch-dedup entries for that task so the dispatcher
  re-emits. Emit a new activity event (`blocked_waiter_autoresumed`) with the
  detected signal for auditability.
- Gate behind a config flag (default off until validated on the fleet), mirror
  the incumbent via a shadow comparison, and keep it a no-op when the signal is
  ambiguous (fail closed to still-blocked, never guess an unblock).

## Not changing

Status lifecycle rules beyond this one transition; owner/reviewer separation;
the requirement that only the waiter's own resolving signal counts (an owner or
helper cannot fabricate the waiter's response); credentials; deploy workflows;
privileged BFF routes; the governed status-command lease semantics.

## Composes with

The supervisor-rewrite state-projection / poll_workers decomposition (this is a
narrow, shippable slice of that), and the existing chair blocked-owner-rescue
path (which should call this instead of declining when the waiter is satisfied).

## Acceptance

- A `blocked` + `waiting_for: Human/Ops` task with a subsequent resolving
  Human/Ops `note` is auto-resumed to an owned-dispatchable status within one
  supervisor cycle, with `waiting_for` cleared, the blocker row resolved, the
  stale dispatch-dedup keys cleared, and a `blocked_waiter_autoresumed` event
  logged.
- An ambiguous / no-signal `blocked` task is left untouched (fail-closed).
- Unit coverage in `.orchestrator/test_supervisor.py` for both the resume and
  the fail-closed path; shadow parity run recorded.
- Flag default-off; enabling it on the fleet is a separate follow-up once shadow
  parity is clean.

## Validation

```text
python3 .orchestrator/test_supervisor.py
python3 -m py_compile .orchestrator/supervisor.py
```
