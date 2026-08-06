# Review Evidence Manifest: SUP-STATUS-OUTBOX-INTEGRITY-VISIBILITY-20260804

- **Task ID**: SUP-STATUS-OUTBOX-INTEGRITY-VISIBILITY-20260804
- **Title**: Make activity-log-integrity-blocked status writes durable and visible instead of silently dropped
- **Owner**: Claude (reassigned from Antigravity 2026-08-06)
- **Reviewer**: Antigravity (reassigned from Claude 2026-08-06)
- **Branch**: `task/SUP-STATUS-OUTBOX-INTEGRITY-VISIBILITY-20260804`
- **Base**: `a64a21a002011a801f07341c5ffc73c8a675e70e` (merge-base with `dev`)
- **Reviewed code commits**:
  - `956ecd4bb5dd94da492f30c567f1294023223d10` — inherited anchor commit (authored by Antigravity before reassignment)
  - `c73b002ad541996cfcc76b5643500538369979a9` — this owner's refinement of that anchor

Both code commits are ancestors of the PR head; this manifest is the one commit
stacked on top of them.

## Dependency Gate

Acceptance 6 sequences this task behind two others. Both are merged into `dev`
ahead of this branch's base:

- `SUP-TASK-FAILURE-STREAK-SCHEMA-20260804` — merged in `062482854` (PR #4564)
- `SUP-PROVIDER-PROBE-HYSTERESIS-20260804` — merged in `a64a21a00` (PR #4581)

## Context Caveat

Acceptance 1 points at `docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md`
section "Problem 3". That file does not exist on `dev` or on this branch (it was
written in the originating chat session and never landed). The repro it describes
is reproduced directly against the code instead, in
`test_integrity_block_persists_pending_markers_on_disk`: a transient
`ActivityAuditInvariantError("activity content-addressed archives do not match
lineage")` raised out of the activity append while a worker's write-back sits in
the outbox.

## Delivered Changes

1. **No integrity check is weakened (acceptance 2).** The diff adds no branch to
   `_validate_active_lineage_head_unlocked`, `prepare_activity_audit_unlocked`,
   `_activity_event_index_unlocked` or any other fail-closed path, and does not
   catch-and-continue anywhere. The single new `except ActivityAuditInvariantError`
   in `recover_status_activity_outbox` records state and **re-raises**; the command
   still fails closed with the same exception and the same exit code.

2. **The blocked write is marked on the task itself (acceptance 3).**
   `_update_pending_outbox_indicators` stamps `status_write_pending: true` and
   `status_write_pending_count: <n>` on every task named by a queued write, across
   both outbox planes (`STATUS_ACTIVITY_OUTBOX_KEY` and
   `STATUS_ARCHIVE_OUTBOX_KEY`). It runs at three points: when
   `commit_state_with_activity_outbox` stages the outbox and saves, when either
   recovery clears its plane, and on the integrity-block path before the re-raise.
   The write itself stays queued for retry; the outbox durability protocol is
   untouched.

3. **Counting is per task, never board-wide.** `_pending_outbox_write_counts`
   counts only queued entries that name a `task_id`. A board-wide event — a
   `wave_open`/`wave_close`, for instance — is not attributed to any task, so it
   cannot make an untouched row look stale. Stamping is total: a task with no
   queued write has both fields removed, so a marker can never outlive its cause.

4. **The board actually shows it (acceptance 4).** This is the part the inherited
   anchor was missing, and it is load-bearing rather than cosmetic: while either
   outbox plane is pending, every read-only status command (`show`, `board`, ...)
   fails closed with `status_recovery_pending` and refuses to render the row at
   all. The derived views are therefore the only readable surface during the
   block, which is why `recover_status_activity_outbox` calls
   `refresh_derived_status_views` before re-raising. `write_current_work` now
   renders, only when at least one task is affected:
   - a `## Status Write Backlog` section naming each affected task, its owner, its
     displayed status and its queued-write count, stating in prose that the rows
     may be stale and that staleness is not evidence the task was untouched; and
   - a `(stale: N writes queued)` suffix on that task's Status cell in both the
     layer tables and the Task Board table.

5. **Transient fields never reach the immutable archive.**
   `archive_terminal_task_from_state` strips both fields from the snapshot it
   stages, and `recover_status_archive_outbox` strips them from both sides of its
   "active terminal task changed during archive recovery" comparison, so a marker
   stamped between staging and recovery cannot fail that guard.

6. **Shipped behind a flag (acceptance 5).**
   `PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED` (`1`/`true`/`yes`/`on`) gates the
   whole feature and defaults to **off**. With the flag off,
   `_update_pending_outbox_indicators` removes the two fields from every task, so
   the canonical rows are exactly the incumbent's, and `write_current_work` emits
   no backlog section and no suffix — rendering is byte-identical to today.
   Turning the flag off after a period of flag-on operation self-cleans on the
   next status write; no migration is needed either way.

7. **Shadow verification is a real assertion.**
   `rewrite.shadow.compare_outbox_indicators` now runs the indicator pass twice
   over the live board and **fails the run (exit 1)** if the flag-off rows are not
   byte-identical to the incumbent rows. The flag-on pass is reported as an
   informational delta. The previous revision of this function printed a count and
   could not fail, and imported `scripts.ai_status` by a package path that does not
   resolve from `.orchestrator` on `sys.path`; it now loads the module by file path.

### Corrections to the inherited anchor commit `956ecd4bb`

- Removed a dead `unbound_count` expression that was computed and never read. The
  anchor's own manifest claimed unbound events "count towards total unbound
  metrics"; no such metric was emitted. Unbound entries are now simply not
  attributed, which is the behaviour the tests assert.
- Replaced the non-failing shadow comparison described above.
- Added the board rendering, without which acceptance 4 was not met: the fields
  existed in `ai-status.json` but no surface a human or auto-worker reads during a
  block displayed them.

## Verification Executed

```bash
/home/lupin/pantheon/.venv/bin/python -m pytest scripts/test_ai_status.py
# -> 192 passed (was 188 before this task; +4 net)

PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/python -m rewrite.shadow \
  --config .orchestrator/config.json --board ai-status.json
# -> max_parallel 16 agents 0 mismatch; account_limit 16 agents 0 mismatch;
#    failure_pause 11 kinds 0 mismatch; dispatch_reason 12 pairs 0 mismatch;
#    outbox_indicators shadow: 6 tasks, 0 mismatch, 0 marked pending when flag enabled
# exit 0
```

### Shadow against a board that actually has queued writes

The live board had no pending outbox, so the flag-on branch was also exercised
against a synthetic copy carrying two writes bound to `MGMT-GAP-001` plus one
unbound event:

```
  pending MGMT-GAP-001: 2 queued status writes
outbox_indicators shadow: 6 tasks, 0 mismatch, 1 marked pending when flag enabled
exit 0
```

Flag-off stayed byte-identical to the incumbent, exactly one task was marked, the
count matched the writes bound to it, and the unbound event marked nothing.

### Tests covering the delivered behaviour

| Test | Asserts |
|---|---|
| `test_integrity_block_persists_pending_markers_on_disk` | With the flag on and the activity append raising the live `ActivityAuditInvariantError`, `ai-status.json` on disk keeps the outbox **and** carries `status_write_pending`/`status_write_pending_count: 1` on the blocked task, an untouched task carries neither, `refresh_derived_status_views` is called once, and the exception still propagates. |
| `test_integrity_block_leaves_board_untouched_when_flag_is_off` | The same block with the flag off leaves every task row free of both fields. |
| `test_status_write_pending_indicators` | Flag on stamps the bound task only; flag off removes markers; a cleared outbox removes markers even with the flag on. |
| `test_status_write_pending_indicators_feature_flag_and_per_task_counting` | Per-task counts (2 for `TASK-A`, 1 for `TASK-B`, none for `TASK-C`); an unbound event does not create board-wide false positives. |
| `test_write_current_work_flags_status_writes_queued_behind_integrity_block` | The backlog section and the `(stale: 2 writes queued)` status suffix render for the affected task and not for the untouched one. |
| `test_write_current_work_omits_backlog_section_without_pending_writes` | With nothing queued, neither the section nor the suffix appears. |

## Rollout

Ship merged and off. To enable, export
`PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED=1` in the supervisor environment that
runs the governed status commands, so both the supervisor and the auto workers it
dispatches inherit it. Enabling it in only one of the two is safe but produces
markers that are stamped by one writer and cleared by the next.

## Known Limitations

- The marker answers "this row may be stale, N writes are queued", not "here is
  what the queued write would have said". Rendering the queued payload would put
  unappended, not-yet-audited content on the board; that is deliberately out of
  scope.
- Only writes that name a `task_id` are attributed. A blocked board-wide event is
  still visible through the fail-closed `status_recovery_pending` diagnostic, but
  it is not attributed to any task row.
- The task whose command is rejected *before* its mutation runs — the pre-command
  `recover_status_activity_outbox` in `run_mutation` — is not itself marked,
  because that write was never staged. The command fails closed to its caller, and
  the tasks behind the block that caused the rejection are marked.
