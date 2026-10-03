# FIFO residual source delivery / coordinator handoff

Scope: OPS-FIFO-FENCE-CLOSEOUT-20261003; owner Codex, reviewer Codex2.
Base: `361fd3b34fb94687a259d677edd1bea2138a50f1`.
Adopted source: [PR5688](https://github.com/ajoe734/pantheon/pull/5688),
`6f683ca985f3ff82bf7fd9e53beab3b28c9ff63b`, authored by ajoe734 with
Claude Fable 5.1. The required task branch and current-dev reconciliation
use a replacement PR. Preserve PR5688 and its history until the replacement
exact head is reviewed, CI passes and the supervisor integrator merges it.
Then close PR5688 as superseded by that proven delivery, without deleting it.
OPS-WORKER-MOUNTS-001 already shipped through PR5669 and is not reopened.

The residual changes directory fence creation, old FIFO upgrade and safe
runtime-source selection. Existing worker receipt atomic-write retries,
promotion drain/health, TaskStore authority and storage layout are retained.
The old PR's idle-wait policy and broad worker refactor are not adopted.
Configured retired writers still fail closed. An ordinary code promotion
keeps all data paths and upgrades only retired FIFO siblings; an explicit
storage move still requires the existing `--migrate-storage` selection.
Linux `renameat2(RENAME_EXCHANGE)` installs the restrictive directory with
no absent-path gap. Unsupported exchange fails with the FIFO intact.
Already-upgraded directories remain fenced on later promotion failure;
rollback does not recreate blocking FIFOs. Real file moves retain the existing
transactional rollback. Retired regular files and symlinks are not overwritten.

## Existing coordinator's bounded promotion/postflight

This is a handoff, not authorization or evidence of hosted execution.
No SSH, VM operations, live FIFO body reads or supervisor restarts were run.
After exact-head merge, the existing coordinator should:

1. Capture the accepted dev commit, incumbent immutable identity and installed
   config identity using the existing governed promotion workflow. Keep its
   configured journal, state and approval paths; do not add a dispatcher/store.
2. Use the current sync/promoter path with the merged source. Its normal
   termination deadline is 15 seconds and health deadline is 600 seconds;
   allow the existing bounded rollback health check to finish too. Collect
   the terminal result and exit code; the existing promoter's `--json` and
   `--evidence-path` options can capture it. A timeout is not a passing promotion.
3. Require `storage_migration.upgraded_fences` to list the converted retired
   FIFO paths. Verify each with `lstat` only: directory, mode 0700, no symlink.
   The ordinary layout has retired state/approval siblings plus journal
   `.lock`/`.head.json` siblings outside the `task-state` directory.
4. Require exact launched PID/runtime identity, fresh healthy canonical
   projection, unchanged configured journal identity and one active TaskStore.
   Verify worker receipt/current-layout reads through existing read-only
   postflight. Never test a live fence by opening or reading its body.
5. If launch/health fails, require the existing rollback receipt and restored
   incumbent identity before accepting service recovery. Preserve upgraded
   directories. Source merge alone proves neither FIFO conversion nor hosted
   product acceptance. Release remains with the existing coordinator.

Review is pending the assigned independent reviewer. Its exact PR/head/manifest
binding and decision belong to canonical task state, not an invented signature
in this file. No post-approval bookkeeping commit should change that head.
