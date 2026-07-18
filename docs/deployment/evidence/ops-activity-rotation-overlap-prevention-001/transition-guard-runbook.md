# Activity Rotation Transition Guard Runbook (rev 3)

Status: required pre-merge runbook; not yet activated. Revision 3 is the
planner correction of revision 2. Revision 2 replaced
the rejected env-threshold design and was composed with
`OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001`, whose stranded
schema-v1 incident both voided the original "no content archive exists"
precondition and proved why the original guard was insufficient.

Revision 3 records that the pending-intent recovery was already executed
exactly once under merge `b122d005bbee3884c77ce6dbe5f225b8f3fe6c1c` and
accepted at evidence head `02861c351fcd6873f60f2c4340c114ad7f296256`
(PR #3788). This transition must verify that result read-only; it must not
execute the recovery transaction again. Revision 3 also makes the required
60-second monitoring cadence and before/after hashes explicit instead of
claiming they exist only in a referenced recovery runbook.

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

The byte-exact crontab stop/restore commands and their fail-closed count and
readback assertions are maintained in:
`../ops-activity-rotation-pending-intent-recovery-001/live-recovery-runbook.md`
(steps 0–2 and 6 only). Steps 3–5 are historical recovery procedure and are
not authorized during this transition.

### Activation snapshot

Before changing cron or stopping a process, create a new task-scoped guard
directory and capture the settings, writer inventory, and active source:

```bash
GUARD_DIR=/tmp/oarop-001/guard-<UTC timestamp>
mkdir -p "$GUARD_DIR"
crontab -l > "$GUARD_DIR/crontab-before.txt"
cp /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  "$GUARD_DIR/supervisor-config-before.json"
sha256sum "$GUARD_DIR/crontab-before.txt" \
  "$GUARD_DIR/supervisor-config-before.json" \
  > "$GUARD_DIR/settings-before.sha256"
jq '{activity_log_rotate_bytes:.paths.activity_log_rotate_bytes,
     max_dispatches_per_tick:.ready_dispatcher.max_dispatches_per_tick,
     max_concurrent_workers:.max_concurrent_workers}' \
  "$GUARD_DIR/supervisor-config-before.json" \
  > "$GUARD_DIR/settings-before.json"
ps -eo pid,ppid,lstart,cmd | rg 'supervisor.py|worker_runner.py|ai_status.py' \
  > "$GUARD_DIR/writers-before.txt" || true
stat -c '%s %Y %i %n' /home/lupin/code/pantheon/ai-activity-log.jsonl \
  > "$GUARD_DIR/activity-before.stat"
sha256sum /home/lupin/code/pantheon/ai-activity-log.jsonl \
  > "$GUARD_DIR/activity-before.sha256"
find /home/lupin/code/pantheon/archive/logs -maxdepth 1 -type f -printf '%f\n' \
  | LC_ALL=C sort | sha256sum > "$GUARD_DIR/archive-list-before.sha256"
```

Record only whether the exact guard key is present in the supervisor
environment; do not dump the environment or secrets. The existing temporary
`PANTHEON_ACTIVITY_ROTATION_PAUSE=1` containment on a running supervisor is
not formal guard activation because worker processes can still write.

Apply the referenced runbook's byte-exact cron guard, export
`PANTHEON_ACTIVITY_ROTATION_PAUSE=1` for any current-code command allowed in
the window, stop the supervisor, and drain all worker-runner chains. If the
writer list is not empty within 10 minutes, restore cron byte-exactly and
abort without merging.

### Continuous readback

After the first empty `ps`/`fuser` readback, capture the following sample at
least every 60 seconds until exact-merge installation and central readback
are complete:

```bash
date -u +%FT%TZ
ps -eo pid,cmd | rg 'supervisor.py|worker_runner.py|ai_status.py' || true
fuser /home/lupin/code/pantheon/.orchestrator/activity-audit.lock || true
fuser /home/lupin/code/pantheon/.orchestrator/task-state.lock || true
stat -c '%s %Y %i %n' /home/lupin/code/pantheon/ai-activity-log.jsonl
sha256sum /home/lupin/code/pantheon/ai-activity-log.jsonl
find /home/lupin/code/pantheon/archive/logs -maxdepth 1 -type f -printf '%f\n' \
  | LC_ALL=C sort | sha256sum
```

Append each timestamped sample to the guard evidence. The writer list and
lock-holder output must remain empty, and active size, inode, mtime, and hash
plus the archive-list digest must remain equal to the activation snapshot.
Any change is an immediate abort: do not merge or install; restore cron and
report a fresh incident.

## Merge-to-install sequence (composed with the pending-intent recovery)

1. Activate the guard (stop + pause env) and complete the readback.
2. Run the recovery inventory command read-only and require the previously
   accepted transaction to report `already_resolved`, with no pending intent.
   The pre-existing content-addressed archive is resolution-superseded, not
   lineage-first. Any other state aborts this transition and requires a fresh
   incident plan. Do not run recovery `execute` again.
3. Merge the independently reviewed exact PR head (auto-merge stays off).
4. Install the exact merge SHA into dev-root and verify `git rev-parse HEAD`
   equals the GitHub merge commit before running project code.
5. Under the still-active guard, run one recovery inventory readback, the
   stable activity-audit assertion, and central logical validation read-only.
   Do not force a central rotation.
6. Restore the crontab from the activation snapshot byte-exactly and verify
   its hash, unset the pause env, verify the supervisor resumes, and run one
   governed status command. Preserve the pre-guard supervisor configuration;
   any later removal of emergency capacity containment is a separately
   recorded operator action after the exact-merge smoke succeeds.
7. Run disposable-root synthetic boundary and multi-rotation proofs plus
   central read-only logical validation. Do not force a central rotation.

Maximum guard window is 45 minutes. Abort and restore if the merge has not
completed by minute 30 or exact install/readback has not completed by minute
40. After merge, an install/readback failure leaves writers stopped and is
escalated; do not restore old code or hand-edit activity state.

## Required acceptance evidence

- writer inventory with observed PIDs/classes before and after;
- guard activation and readback transcripts (ps/fuser outputs);
- pre-guard activity stat/hash, archive-list digest, and 60-second unchanged
  samples;
- pre-guard supervisor config values/hash and exact guard-key presence only;
- crontab before/after byte-equality;
- the accepted prior single-execute evidence (`02861c351fcd...`) plus new
  pre/post-install read-only `already_resolved` inventories; no new execute;
- exact merge SHA installed into dev-root;
- supervisor/status resume evidence.
