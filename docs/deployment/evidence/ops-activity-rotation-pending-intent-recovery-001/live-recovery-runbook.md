# Live guarded recovery runbook — pending-intent resolution

Status: NOT executed. Requires planner acceptance of the merged PR, exact
dev-root installation of the reviewed merge SHA, and independent review
sign-off first. One approved execution only.

Operator preconditions: merged PR head installed at the exact reviewed SHA
in `/home/lupin/pantheon-ci-deploy/dev-root`; this runbook, the pinned
inventory digest, and the attestation text approved by the planner.

## 0. Record state (read-only)

```bash
mkdir -p /tmp/oparpir-001/guard
crontab -l > /tmp/oparpir-001/guard/crontab-before.txt
ps -eo pid,ppid,lstart,cmd | grep -E "supervisor.py|worker_runner.py" | grep -v grep \
  > /tmp/oparpir-001/guard/writers-before.txt
stat -c '%s %Y %i %n' /home/lupin/code/pantheon/ai-activity-log.jsonl
```

## 1. Stop all writers (the enforceable all-writer guard)

```bash
# 1a. Pause the supervisor respawner. Derive the guarded crontab from the
#     byte-exact backup taken in step 0 — never from a fresh `crontab -l`
#     read — and fail closed on every count/readback mismatch. The live
#     respawner is the single active cron line invoking
#     `scripts/run-supervisor-watchdog.sh ... --restart` and ending with the
#     `# pantheon-supervisor-watchdog` marker.
GUARD_DIR=/tmp/oparpir-001/guard
WATCHDOG_RE='scripts/run-supervisor-watchdog\.sh .*# pantheon-supervisor-watchdog$'

# Exactly one ACTIVE (non-comment) supervisor-watchdog line must exist
# before any mutation; otherwise abort with no change.
ACTIVE_BEFORE=$(grep -cE "^[^#].*${WATCHDOG_RE}" "$GUARD_DIR/crontab-before.txt")
[ "$ACTIVE_BEFORE" -eq 1 ] || { echo "ABORT: expected exactly 1 active supervisor-watchdog cron line, found ${ACTIVE_BEFORE}; no mutation performed"; exit 1; }

# Comment exactly that one line with the guard marker.
sed -E "s|^([^#].*scripts/run-supervisor-watchdog\.sh .*# pantheon-supervisor-watchdog)$|#GUARD-OPARPIR-001 \1|" \
  "$GUARD_DIR/crontab-before.txt" > "$GUARD_DIR/crontab-guarded.txt"

# Verify the generated guarded crontab differs exactly as expected:
# not a no-op, exactly one guard marker, zero remaining active watchdog
# lines, and a one-line-only difference from the backup.
cmp -s "$GUARD_DIR/crontab-before.txt" "$GUARD_DIR/crontab-guarded.txt" \
  && { echo "ABORT: guard would be a no-op; no mutation performed"; exit 1; }
[ "$(grep -c '^#GUARD-OPARPIR-001 ' "$GUARD_DIR/crontab-guarded.txt")" -eq 1 ] \
  || { echo "ABORT: guarded crontab does not contain exactly one guard marker; no mutation performed"; exit 1; }
[ "$(grep -cE "^[^#].*${WATCHDOG_RE}" "$GUARD_DIR/crontab-guarded.txt")" -eq 0 ] \
  || { echo "ABORT: an active supervisor-watchdog line survived guarding; no mutation performed"; exit 1; }
[ "$(diff "$GUARD_DIR/crontab-before.txt" "$GUARD_DIR/crontab-guarded.txt" | grep -c '^[<>]')" -eq 2 ] \
  || { echo "ABORT: guarded crontab differs by more than the one expected line; no mutation performed"; exit 1; }

# Install the guarded crontab, then immediately byte-compare the live
# readback against the generated file. On mismatch, restore the byte-exact
# backup and abort.
crontab "$GUARD_DIR/crontab-guarded.txt"
crontab -l > "$GUARD_DIR/crontab-guard-readback.txt"
cmp "$GUARD_DIR/crontab-guarded.txt" "$GUARD_DIR/crontab-guard-readback.txt" \
  || { crontab "$GUARD_DIR/crontab-before.txt"; echo "ABORT: cron readback mismatch; byte-exact backup restored"; exit 1; }

# 1b. Belt-and-suspenders: current-code writers refuse rotation/recovery.
#     (Set in the supervisor launch environment for the restart window.)
export PANTHEON_ACTIVITY_ROTATION_PAUSE=1

# 1c. Stop the supervisor (TERM, then verify exit).
kill <supervisor_pid>

# 1d. Let live worker_runner chains drain; do not start new dispatch.
```

## 2. Verify no writer remains (readback)

```bash
ps -eo pid,cmd | grep -E "supervisor.py|worker_runner.py|ai_status" | grep -v grep   # expect empty
fuser /home/lupin/code/pantheon/.orchestrator/activity-audit.lock                    # expect empty
fuser /home/lupin/code/pantheon/.orchestrator/task-state.lock                        # note holders; must be none from writer classes
```

No manual `scripts/ai-status.sh` / `ai_status.py` commands may run against
the central root during the window (operator discipline; the pending intent
also keeps every governed command fail-closed until resolution).

## 3. Re-pin and dry-run (read-only)

```bash
cd /home/lupin/pantheon-ci-deploy/dev-root
python3 .orchestrator/activity_pending_intent_recovery.py inventory \
  --status-root /home/lupin/code/pantheon \
  --output /tmp/oparpir-001/guard/pin.json
python3 .orchestrator/activity_pending_intent_recovery.py dry-run \
  --status-root /home/lupin/code/pantheon \
  --inventory /tmp/oparpir-001/guard/pin.json \
  --output /tmp/oparpir-001/guard/dry-run.json
```

Record the printed `inventory_sha256`; the planner approves that exact
digest. Dry-run must report `status: resolvable`. Any drift from the
approved incident shape stops the procedure (report, restore, no mutation).

## 4. Execute once (the only mutating step)

```bash
PANTHEON_ACTIVITY_PENDING_INTENT_RECOVERY_EXECUTE=I-UNDERSTAND-LIVE-MUTATION \
python3 .orchestrator/activity_pending_intent_recovery.py execute \
  --status-root /home/lupin/code/pantheon \
  --inventory /tmp/oparpir-001/guard/pin.json \
  --expected-inventory-sha256 <approved digest from step 3> \
  --writer-guard-attestation "<operator>: supervisor stopped, respawn cron paused, worker chains drained, readback empty at <UTC time>" \
  --output /tmp/oparpir-001/guard/execute-report.json
```

Execute takes the exclusive activity lock, re-verifies every pinned byte
(including active sha/inode), preserves the intent + staged files, appends
the resolution row, removes the pending marker, and reads everything back.
On crash, re-run the same command with the SAME pin; the transaction is
idempotent and converges (proven by the SIGKILL matrix).

## 5. Post-recovery readback (read-only)

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, ".orchestrator")
from pathlib import Path
import common
log = Path("/home/lupin/code/pantheon/ai-activity-log.jsonl")
common.assert_activity_audit_stable_unlocked(log)
print("sources:", len(common.activity_audit_source_paths_unlocked(log)))
PY
```

Then run the central read-only logical validation and one governed
`show`-class smoke command after writers resume.

## 6. Restore writers

```bash
crontab /tmp/oparpir-001/guard/crontab-before.txt   # byte-exact restore
unset PANTHEON_ACTIVITY_ROTATION_PAUSE
# supervisor respawns via cron; verify:
ps -eo pid,cmd | grep supervisor.py | grep -v grep
```

Verify one supervisor cycle and one governed status command complete
normally, then capture the after-inventory:

```bash
python3 .orchestrator/activity_pending_intent_recovery.py inventory \
  --status-root /home/lupin/code/pantheon \
  --output /tmp/oparpir-001/guard/after.json
```

(The after-inventory reports `already_resolved` accounting; active appends
after resume are expected and healthy.)

## Window / abort

- Maximum guard window: 45 minutes from step 1. If step 4 has not STARTED
  by minute 30, abort.
- Abort = do not run execute; restore crontab; report. Steps 1–3 make no
  mutation, so abort is always clean before step 4.
- After step 4 has completed successfully there is no rollback that deletes
  the resolution; the preserved copies plus untouched originals are the
  audit trail. If step 4 fails mid-way, the state remains fail-closed and
  idempotent re-execution (same pin) is the designed path; escalate to the
  planner rather than hand-editing anything.
- Abort thresholds: any writer-class process observed during the window;
  any digest mismatch reported by dry-run/execute; any archive-listing
  change; the activity lock held by an unknown PID at step 4.
