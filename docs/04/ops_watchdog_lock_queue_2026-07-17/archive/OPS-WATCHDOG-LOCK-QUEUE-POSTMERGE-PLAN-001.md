# Watchdog lock-contention post-merge acceptance plan — 2026-07-17

## Purpose

Close the acceptance gap left after implementation PR #3790 merged. This is an
acceptance/evidence plan, not a second implementation. The merged code remains
the only watchdog contract; the fleet must prove it is installed and behaves
correctly in the Pantheon dev runtime.

Implementation merge:

- PR: `https://github.com/ajoe734/pantheon/pull/3790`
- merge commit: `3705570e4e2a2cb26ba282603a9ca36f0da3c228`

## Known evidence defect

The merged evidence file
`docs/04/ops_watchdog_lock_queue_2026-07-17/OPS-WATCHDOG-LOCK-QUEUE-EVIDENCE-001.md`
claims 32 tests, while the exact implementation worktree rerun produced 33
passing tests. It also contains fixture/process evidence but no three-cycle
dev-root scheduler readback. The original task therefore stays `review` and
must not be marked done from the merge alone.

## Execution task

`OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001` is owned by a currently admitted fleet
identity and reviewed by a different admitted identity. It targets `pantheon`
`dev`, keeps auto-merge off, and changes evidence/runbook files only unless a
separately reviewed implementation defect is reproduced.

## Required acceptance

1. Start from current `origin/dev` and bind every result to the exact installed
   implementation SHA and the dev-root checkout SHA. If dev-root is stale,
   use the documented controlled sync/maintenance procedure; do not reset a
   dirty shared checkout or edit runtime files by hand.
2. Rerun the exact watchdog test suite and record the actual count (currently
   expected to be 33), command, commit, and result. Evidence must not retain
   the stale 32-test claim.
3. Under a documented held-lock fixture, launch at least 10 cron-equivalent
   probes and prove each exits within the bound, reports structured
   `decision=skip/reason=lock_contention`, and leaves no accumulating waiter
   process. Include the secondary contention-metric lock case.
4. Release the lock and capture three consecutive real scheduler/watchdog
   cycles. Each cycle must show a fresh probe, the supervisor PID/checkout
   identity, and `supervisor_runtime_health.py --require-watchdog --json`
   healthy. A single unit test or one manual probe is insufficient.
5. Keep state and metric files hash-bound before/after; prove contention does
   not write locked watchdog state and that any dropped metric is explicitly
   reported. Do not claim healthy when the watchdog sample is stale.
6. Publish redacted post-merge evidence through an evidence-only PR. The PR
   must not alter `.orchestrator/supervisor_watchdog.py`, registry/config, or
   canonical activity/task state.

## Safety and stop gates

- Do not kill unrelated workers, remove lock files, truncate logs, or hand-edit
  runtime state to make health green.
- Stop if the supervisor is not running, dev-root identity cannot be proven,
  the watchdog sample is stale, the three-cycle window is interrupted, or the
  installed SHA differs from the reviewed merge.
- Any implementation defect found becomes a new planner-approved task; this
  acceptance task may not silently patch product code.

## Completion

The original implementation task may move from `review` only after this
evidence task is independently approved, its evidence PR is merged, and the
three-cycle live readback is archived. Until then, the watchdog fix is merged
code but not a product-level accepted runtime capability.
