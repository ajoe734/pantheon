# OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001 Evidence

Status: pre-review owner evidence for the P0 activity rotation follow-up.

## Scope

Owned implementation:

- `.orchestrator/common.py`
- `.orchestrator/test_common.py`
- `scripts/ai_status.py`
- `scripts/test_ai_status.py`
- `.orchestrator/task-briefs/ops_activity_rotation_overlap_prevention_001.md`

Not changed:

- product trading behavior
- BFF/frontend/provider routing
- central status files or central activity payload bytes
- legacy archive bytes
- dev-root install or live rotation state

## Implementation Summary

- Added schema v2 content-addressed rotation lineage in
  `.orchestrator/logs/activity-rotation/<log>.lineage.jsonl`.
- Added active lineage-head control records at the beginning of the active log
  after every completed content-addressed rotation, including `keep_lines=0`.
- Changed content-addressed source enumeration to use lineage order after
  legacy timestamp sources and before active; lexical hash filename ordering is
  rejected as an authority.
- Added the one-time first content rotation boundary normalization: only the
  exact 1,000-line active prefix matching the immediately preceding legacy
  timestamp archive suffix may be excluded from the first content archive.
- Extended restart recovery through intent, archive publish, active tail/control
  publish, and lineage publish. Intent remains until all readback checks pass.
- Preserved fail-closed overlap rejection for content-addressed archives and
  added fail-closed checks for unregistered/missing/tampered content archives,
  sequence gaps/forks, duplicate sequence/transaction/archive, stale/missing
  active control, second boundary exception, symlink leaves, and unstable
  sources.
- Hardened restart recovery so pending intent, staged archive, staged tail,
  installed archive, and lineage reads all use stable regular-file checks and
  reject symlink leaves.
- Generalized activity source classification to the configured `.jsonl` log
  basename so supervisor/common writers using non-default isolated test log
  names share the same content-addressed lineage contract.
- Added a bounded structured fail-closed diagnostic for the 2026-07-17
  non-adjacent-tail addendum. The reader now raises
  `ActivityAuditInvariantError` with `activity_non_adjacent_tail`,
  evidence digest, matched source, immediate predecessor, current source, and
  prefix/suffix digest. Read-only `ai-status show/prompt` converts that into a
  JSON fail-closed diagnostic instead of an unstructured traceback.
- Added the exact non-adjacent-tail fixture with a lineage-registered
  content-addressed archive named
  `ai-activity-log.jsonl-b320711ea85d1a0bfd537f39a0c934b4b865ce0805ff389df0405a3a89d5d004.gz`
  and a bounded deadline assertion.
- Tightened task archive idempotency discovered during full status validation:
  an existing archive snapshot may be reused only when task identity,
  terminal outcome, handoffs, and blockers match the active terminal task.

## Central Read-Only Preconditions

Commands run from the task worktree:

```bash
find /home/lupin/code/pantheon/archive/logs -maxdepth 1 -type f -regextype posix-extended -regex '.*/ai-activity-log\.jsonl-[a-f0-9]{64}\.gz' -print
find /home/lupin/code/pantheon/.orchestrator/logs/activity-rotation -maxdepth 1 -type f -name 'ai-activity-log.jsonl.lineage.jsonl' -print
```

Result: both commands returned no paths. The central root still had no
content-addressed activity archive and no activity lineage file at pre-review
time. No central activity payload bytes were copied into this evidence.

Continuation note: after composing the 2026-07-17 non-adjacent-tail addendum,
this implementation continuation did not open or lock the central
activity/status root. All new fixtures and validation ran against repo-external
or test-local roots.

## Validation

Commands run from the task worktree:

```bash
python3 -m py_compile .orchestrator/common.py .orchestrator/test_common.py scripts/ai_status.py scripts/test_ai_status.py
python3 .orchestrator/test_common.py
python3 -m unittest scripts.test_activity_audit_logical_inventory
env -u ORCH_RUN_ID -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PANTHEON_STATUS_ROOT=/tmp/pantheon-ops-activity-rotation-test-status-root python3 -m unittest scripts.test_ai_status
python3 .orchestrator/test_supervisor_watchdog.py
python3 .orchestrator/test_worker_runner_heartbeat.py
python3 .orchestrator/test_runtime_state.py
env -u PANTHEON_STATUS_ROOT -u ORCH_RUN_ID -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH timeout 240 python3 .orchestrator/test_supervisor.py
git diff --check
```

Results:

- `py_compile`: passed.
- `.orchestrator/test_common.py`: 65 tests passed.
- `scripts.test_activity_audit_logical_inventory`: 19 tests passed.
- isolated `scripts.test_ai_status`: 75 tests passed.
- `.orchestrator/test_supervisor_watchdog.py`: 33 tests passed.
- `.orchestrator/test_worker_runner_heartbeat.py`: 13 tests passed.
- `.orchestrator/test_runtime_state.py`: passed.
- isolated `.orchestrator/test_supervisor.py`: 277 tests passed.
- `git diff --check`: passed.

## PR Check Readback

PR #3797 was opened with auto-merge off as required by the task brief. The PR
event check run for head `3e0372ed1` passed Commit trailers, Runtime mirror
guard, Forward to orchestrator, and Smoke acceptance. A separate push-event
Commit trailers run failed because it scanned the stale remote-task range from
`577af8f9c` through already-merged dev commits, including a pre-existing
subject-length violation outside the PR diff. This evidence refresh commit
exists to make the final task-branch push range narrow to this task head.

A plain inherited-env `scripts.test_ai_status` run was interrupted after it
attempted to use the central `PANTHEON_STATUS_ROOT`; the accepted validation is
the isolated-root command listed above.

A plain inherited-env `.orchestrator/test_supervisor.py` run reached the
discussion-planning dispatch section and timed out while inheriting the
auto-worker central `PANTHEON_STATUS_ROOT`. The accepted supervisor validation
is the isolated-env command listed above; the previously stuck single test
passed under that environment before the full isolated suite passed.

## Status Command Note

`AI_NAME=Codex2 ./scripts/ai-status.sh start
OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001 ...` was attempted through the
governed status command path with a 20-second timeout. Readback later showed
the task did move to `in_progress` with the intended start message, but the
CLI did not return before timeout because central locks were held by another
worker status command. No manual edit was made to `ai-status.json`.

Lock holder readback at the time:

- task-state lock: PID `3080733`,
  `/tmp/pantheon-reconciliation-json-store-integrity/scripts/ai_status.py
  approve OPS-RECONCILIATION-JSON-STORE-INTEGRITY-001 ...`
- activity-audit lock: supervisor PID `2963491` and PID `3080733`

`scripts/git/worker_commit.py` created anchor commit `f3157e33a`, then blocked
on the post-commit central activity audit append. The wrapper was interrupted
after the commit was already durable; `git status --short` was clean
afterward.

## Transition Guard

The required all-writer transition guard is documented in
[`transition-guard-runbook.md`](transition-guard-runbook.md). The guard has not
been activated by this pre-review implementation run.
