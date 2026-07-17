# Activity Rotation Transition Guard Runbook (rev 2)

Status: required pre-merge runbook; not yet activated. Revision 2 replaces
the rejected env-threshold design and is composed with
`OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001`, whose stranded
schema-v1 incident both voided the original "no content archive exists"
precondition and proved why the original guard was insufficient.

## Why revision 2

The planner rejected the rev 1 guard as unenforceable:

- `AI_STATUS_LOG_ROTATE_MAX_BYTES` is read only by `scripts/ai_status.py`;
- supervisor/common writers read `config paths.activity_log_rotate_bytes`
  through `.orchestrator/common.py::_activity_log_rotate_threshold()`;
- old-vintage worker worktree checkouts read neither — one such writer
  produced the 2026-07-16T2337Z legacy rotation while a current-code v1
  intent was pending, which is the live incident.

No environment threshold override can cover every writer class. The guard
must stop writers and prove they are stopped.

## Writer classes (from the 2026-07-17 live inventory)

See `../ops-activity-rotation-pending-intent-recovery-001/writer-inventory.md`
for the captured instance table:

1. supervisor + its in-process watchers (dev-root `common.py` path);
2. governed status commands from worker worktrees (mixed code vintages);
3. manual operator status commands;
4. cron entries (currently none touch the activity log);
5. read-side processes (no rotation capability).

## The enforceable guard

1. **Code-level pause for current-code writers.**
   `PANTHEON_ACTIVITY_ROTATION_PAUSE=1`
   (`common.ACTIVITY_ROTATION_WRITER_GUARD_ENV`) is honored inside
   `rotate_activity_log_unlocked` and `prepare_activity_audit_unlocked`,
   the shared choke points both writer mechanisms funnel through. While
   set, no new rotation starts and no pending intent is recovered; appends
   remain prefix-safe. Covered by tests
   (`StrandedIntentFailClosedTests.test_writer_guard_pauses_*`).
2. **Process stop for everything else (and belt-and-suspenders for 1).**
   Pause the supervisor respawn cron, stop the supervisor, drain live
   worker_runner chains, and forbid manual status commands for the window.
3. **Readback.** `ps` for writer-class processes must be empty and `fuser`
   on `.orchestrator/activity-audit.lock` / `task-state.lock` must show no
   writer-class holder before any merge-to-install action.

Exact commands, the monitoring cadence, the 45-minute window, and the abort
thresholds are maintained in one place:
`../ops-activity-rotation-pending-intent-recovery-001/live-recovery-runbook.md`
(steps 0–2 and 6 are the guard; steps 3–5 are the recovery-specific parts).

## Merge-to-install sequence (composed with the pending-intent recovery)

1. Activate the guard (stop + pause env) and complete the readback.
2. Merge the reviewed composed PR (auto-merge stays off; planner decision).
3. Install the exact merge SHA into dev-root.
4. Run the guarded pending-intent recovery (pin → dry-run → single execute
   → readback) per the live recovery runbook. The pre-existing
   content-addressed archive is resolution-superseded, NOT lineage-first;
   do not apply the original one-time boundary rule blindly.
5. Restore the crontab byte-exactly, unset the pause env, verify the
   supervisor resumes and one governed status command succeeds.
6. Run disposable-root synthetic boundary and multi-rotation proofs plus
   central read-only logical validation. Do not force a central rotation.

## Required acceptance evidence

- writer inventory with observed PIDs/classes before and after;
- guard activation and readback transcripts (ps/fuser outputs);
- crontab before/after byte-equality;
- pinned inventory digest, dry-run report, single execute report, and
  post-recovery readback from the recovery runbook;
- exact merge SHA installed into dev-root;
- supervisor/status resume evidence.
