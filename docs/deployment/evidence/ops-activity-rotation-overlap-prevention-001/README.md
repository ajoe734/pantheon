# OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001 Evidence

Status: exact-head owner evidence after composing current `origin/dev`
`deab7fc14686f428dbdfa5745288db998a0e7f2d`. Auto-merge remains disabled;
independent exact-head review is still required.

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
- Added the exact non-adjacent-tail fixture with a genuinely content-addressed
  lineage archive whose basename is derived from its payload digest, plus a
  bounded deadline assertion.
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

## 2026-07-17 Control-Plane Recovery Composition

The latest composition closes the review findings that were not covered by
the original pre-review evidence:

- the first boundary row now reopens its exact predecessor and verifies full
  payload digest/bytes/lines plus the excluded 1,000-line suffix before any
  logical row is exposed; replacing that gzip with only its non-overlap prefix
  fails closed instead of losing 1,000 rows;
- every content-addressed archive basename must equal its decompressed payload
  SHA-256, including lineage and resolution rows;
- fault injection covers durable `stage_archive` and `stage_tail` boundaries
  in addition to intent/archive/tail/lineage publication;
- the transition guard refuses append behind a pending intent, preserving the
  recovery source digest;
- active, ancestor, archive-directory, lineage, resolution, backup, and stage
  symlink components fail closed, including dangling control-file symlinks;
- all runtime integrity failures are normalized to the structured
  `pantheon.activity.fail_closed.v1` diagnostic contract;
- read-only `show`/`prompt` use one nonblocking shared task-state lock, never
  recover or mutate an outbox, and return bounded JSON diagnostics for a busy
  writer or pending recovery;
- a normal mutation marks its freshly persisted activity outbox as known
  unappended, appends it once, and verifies it from the bounded active tail.
  Restart recovery still validates complete history to detect cross-source
  duplicates;
- automatic supervisor task-brief generation no longer validates hundreds of
  global activity archives merely to render six redundant recent rows. The
  canonical task row is the bounded dispatch context; complete activity
  validation remains available for explicit forensic readers.
- normal appends use a stable `O_NOFOLLOW` descriptor/leaf identity and size
  check before entering rotation. An active log below the configured threshold
  no longer enumerates immutable history on every event; once the threshold is
  exceeded, rotation still performs the complete lineage/history validation
  before publishing or appending anything.

All validation used test-local or repo-external status roots. One initial
unisolated status-suite invocation rotated the isolated task worktree's tracked
activity fixture; that Codex-owned test mutation was restored byte-for-byte
from the task branch before any commit. It did not address or mutate the live
central activity root.

Final composition commands and results:

```bash
python3 .orchestrator/test_common.py
PANTHEON_STATUS_ROOT=/tmp/pantheon-ai-status-tests-... python3 -m unittest scripts.test_ai_status
python3 -m unittest scripts.test_activity_audit_logical_inventory scripts.test_status_file_guard
python3 .orchestrator/test_activity_pending_intent_recovery.py
python3 .orchestrator/test_runtime_state.py
python3 .orchestrator/test_supervisor_watchdog.py
python3 .orchestrator/test_worker_runner_heartbeat.py
env -u PANTHEON_STATUS_ROOT -u ORCH_RUN_ID -u PANTHEON_WORKTREE_ROOT \
  -u ORCH_WORKSPACE_PATH -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH \
  -u PANTHEON_COMMAND_ROOT -u PANTHEON_COMMAND_RUNTIME_SHA \
  timeout 300 python3 .orchestrator/test_supervisor.py
python3 -m unittest scripts.test_status_command_runtime_pin
python3 .orchestrator/test_task_archive_index_legacy_id.py
python3 -m py_compile <changed Python files>
git diff --check
```

Results before the final evidence-only commit:

- common: 90 passed;
- isolated ai-status: 81 passed;
- inventory/status guard: 41 passed, 1 explicit opt-in skip;
- pending intent/resolution: 37 passed;
- runtime state: passed;
- supervisor watchdog: 43 passed;
- worker runner: 22 passed;
- supervisor: 277 passed;
- status-command runtime pin: 6 passed;
- task archive index: passed;
- `py_compile` and `git diff --check`: passed.

### Live read-only observation

A 44-second, three-sample read-only window at `22:54:17Z`–`22:55:01Z`
showed no net completion: the board remained `todo=19`, `in_progress=4`,
`review=8`, `blocked=7`; archive count remained 2,393; and the newest terminal
archive was still timestamped `17:47:56Z`. Recent worker exits were mixed
(`0=4`, `143=3`), while the supervisor alone consumed roughly 35% CPU under
shared task/activity locks. This is direct evidence that process activity did
not equal task completion and that automatic full-history task-brief reads
were still starving dispatch before this patch.

A subsequent live read-only descriptor sample found one supervisor cycle
reopening the 423-source immutable archive set more than once while the active
log was still roughly 5 MiB against a 50 MiB rotation threshold. That second
append-side scan source is what the stable size gate removes. The two new
regressions prove a below-threshold append never opens immutable history and an
above-threshold append still fails closed when history validation fails.
