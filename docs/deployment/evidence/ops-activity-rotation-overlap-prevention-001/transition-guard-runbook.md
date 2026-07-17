# Activity Rotation Transition Guard Runbook

Status: required pre-merge runbook; not yet activated.

## Purpose

Prevent any live status/supervisor writer from performing the old
content-addressed rotation between merge and exact dev-root installation of
`OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001`.

## Writer Coverage

The repository writer inventory was produced with:

```bash
rg -n "write_activity_log\(|append_log\(|append_activity_log_entries_unlocked\(|maybe_rotate_activity_log\(|rotate_activity_log_unlocked\(" .orchestrator scripts -g '!*.pyc'
rg -n "activity_log_rotate_bytes|LOG_ROTATE_MAX_BYTES|AI_STATUS_LOG_ROTATE_MAX_BYTES" .orchestrator scripts docs -g '!*.json'
```

Writer classes covered by this guard:

- governed status commands in `scripts/ai_status.py`
- supervisor, watchdog, GitHub bus, permission, approval, and coordination
  watcher writes through `.orchestrator/common.py::write_activity_log`
- worker commit audit writes through `scripts/git/worker_commit.py`
- loop/product dispatch scripts that call `append_activity_log_entries_unlocked`
  or local `append_log` helpers

## Preferred Guard

Use a temporary all-writer rotation threshold override so writers may append
but cannot rotate before the exact merge is installed.

Guard value:

```text
AI_STATUS_LOG_ROTATE_MAX_BYTES=1073741824
```

This is intentionally above the observed pre-review active log size and below
an unbounded value, so growth remains observable and abortable.

## Activation Checklist

1. Record active writer processes and command environments for supervisor,
   watchdog, GitHub bus, approval queue, coordination watcher, and live worker
   runners.
2. Record old `AI_STATUS_LOG_ROTATE_MAX_BYTES` value for each writer
   environment. If unset, record `unset`.
3. Record active log size and SHA-256 without copying payload bytes:

   ```bash
   stat -c '%s %n' /home/lupin/code/pantheon/ai-activity-log.jsonl
   sha256sum /home/lupin/code/pantheon/ai-activity-log.jsonl
   ```

4. Verify no content-addressed archive or lineage exists:

   ```bash
   find /home/lupin/code/pantheon/archive/logs -maxdepth 1 -type f -regextype posix-extended -regex '.*/ai-activity-log\.jsonl-[a-f0-9]{64}\.gz' -print
   find /home/lupin/code/pantheon/.orchestrator/logs/activity-rotation -maxdepth 1 -type f -name 'ai-activity-log.jsonl.lineage.jsonl' -print
   ```

5. Apply the override to every listed writer process environment through the
   supervisor-owned launch/configuration path. Do not edit central state files
   by hand.
6. Restart only the writer processes required to pick up the override, using
   the normal supervisor/watchdog procedure.
7. Read back every writer environment and confirm the override is present.

## Monitoring Window

Maximum duration: 45 minutes from activation.

Abort thresholds:

- any content-addressed archive appears before exact merge installation
- active log reaches 900 MiB
- any writer lacks the override after restart/readback
- any governed status command fails for an activity lineage/control reason
- dev-root installation cannot start within 20 minutes of guard activation

Monitor every 2 minutes:

```bash
stat -c '%s %n' /home/lupin/code/pantheon/ai-activity-log.jsonl
find /home/lupin/code/pantheon/archive/logs -maxdepth 1 -type f -regextype posix-extended -regex '.*/ai-activity-log\.jsonl-[a-f0-9]{64}\.gz' -print
find /home/lupin/code/pantheon/.orchestrator/logs/activity-rotation -maxdepth 1 -type f -name 'ai-activity-log.jsonl.lineage.jsonl' -print
```

## Merge-To-Install Sequence

1. Activate the guard and confirm all writer readbacks.
2. Merge the reviewed task PR with auto-merge still off unless the planner
   explicitly changes that decision.
3. Install the exact merge SHA into dev-root.
4. Before restoring normal thresholds, run a read-only check that there is
   still no central content-addressed archive or lineage. If either exists,
   stop and create a fresh incident inventory.
5. Restore each writer's old `AI_STATUS_LOG_ROTATE_MAX_BYTES` value.
6. Restart/read back writers again.
7. Run disposable-root synthetic boundary and multi-rotation proofs.
8. Run central read-only logical validation and governed show/note smoke.

## Restoration

For each writer environment:

- if the old value was `unset`, remove `AI_STATUS_LOG_ROTATE_MAX_BYTES`
- otherwise restore the recorded value exactly

Then restart through supervisor-owned process controls and read back the
effective environment. Do not force a central live rotation as a smoke test.

## Required Acceptance Evidence

- writer list and old/new environment values
- active size and SHA-256 before activation, before install, and after restore
- no-content-archive/no-lineage readback before merge and before install
- exact merge SHA installed into dev-root
- restoration readback for every writer
- disposable-root boundary and three-content-archive lineage proof
- central read-only logical validation
- governed show/note smoke result
