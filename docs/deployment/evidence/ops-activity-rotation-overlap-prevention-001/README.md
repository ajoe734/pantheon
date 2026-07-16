# OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001 Evidence

Status: pre-review owner evidence for the P0 activity rotation follow-up.

## Scope

Owned implementation:

- `.orchestrator/common.py`
- `.orchestrator/test_common.py`
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

## Central Read-Only Preconditions

Commands run from the task worktree:

```bash
find /home/lupin/code/pantheon/archive/logs -maxdepth 1 -type f -regextype posix-extended -regex '.*/ai-activity-log\.jsonl-[a-f0-9]{64}\.gz' -print
find /home/lupin/code/pantheon/.orchestrator/logs/activity-rotation -maxdepth 1 -type f -name 'ai-activity-log.jsonl.lineage.jsonl' -print
```

Result: both commands returned no paths. The central root still had no
content-addressed activity archive and no activity lineage file at pre-review
time. No central activity payload bytes were copied into this evidence.

## Validation

Commands run from the task worktree:

```bash
python3 -m py_compile .orchestrator/common.py .orchestrator/test_common.py scripts/ai_status.py scripts/test_ai_status.py
python3 .orchestrator/test_common.py
python3 -m unittest scripts.test_activity_audit_logical_inventory
python3 -m unittest scripts.test_ai_status
git diff --check
```

Results:

- `py_compile`: passed.
- `.orchestrator/test_common.py`: 58 tests passed.
- `scripts.test_activity_audit_logical_inventory`: 19 tests passed.
- `scripts.test_ai_status`: 74 tests passed.
- `git diff --check`: passed.

## Status Command Note

`AI_NAME=Codex2 ./scripts/ai-status.sh start
OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001 ...` was attempted through the
governed status command path with a 20-second timeout and did not acquire the
central task-state lock. No manual edit was made to `ai-status.json`.

`scripts/git/worker_commit.py` created anchor commit `f3157e33a`, then blocked
on the post-commit central activity audit append. The wrapper was interrupted
after the commit was already durable; `git status --short` was clean
afterward.

## Transition Guard

The required all-writer transition guard is documented in
[`transition-guard-runbook.md`](transition-guard-runbook.md). The guard has not
been activated by this pre-review implementation run.
