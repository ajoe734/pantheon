# OPS-STATUS-COMMAND-RUNTIME-PIN-001 Evidence

Status: pre-merge owner evidence for PR #3783.

## Scope

This evidence covers the code-level command runtime pin, active worker lease
validation, synthetic stale-worker regressions, and the cutover gate that must
run after merge. It does not claim that the reviewed SHA has been installed or
that postmerge stale-worktree proof has completed.

## Candidate

- Candidate branch: `task/OPS-STATUS-COMMAND-RUNTIME-PIN-001`
- Candidate SHA before this follow-up commit:
  `4a07c572b8805f4d2b4b77c8d49d9ac221628126`
- PR: `https://github.com/ajoe734/pantheon/pull/3783`
- Merge target: `dev`
- Reviewer required by corrected brief: `Claude`
- Earlier pushed commit `9fedc01053220c59da7a0e508af6b31969ea3db8`
  contains `Reviewer: Antigravity`. That commit was already pushed before the
  corrected brief/review hold; it is preserved without force-push. Follow-up
  commits and review ownership use `Reviewer: Claude`.

## Implemented Guard

- Supervisor records one issued `status_command_runtime` on the worker record
  and request snapshot: command root, source SHA, remote, and base ref.
- Adapters inherit that issued runtime instead of recomputing a different
  command root/SHA later.
- Mutating `scripts/ai_status.py` commands with `ORCH_RUN_ID` take the runtime
  shared lock before task-state EX and validate:
  - worker record exists for the run id;
  - worker status is active and lease expiry is in the future;
  - argv task, `ORCH_TASK_ID`, and worker task agree;
  - worker status root equals `PANTHEON_STATUS_ROOT`;
  - worker workspace equals `PANTHEON_WORKTREE_ROOT` / `ORCH_WORKSPACE_PATH`;
  - running command root/SHA equals the supervisor-issued command runtime;
  - a matching worktree lease exists for the task/workspace/status root.
- Controlled commands without `ORCH_RUN_ID` retain command root/SHA/status-root
  validation and do not require an active worker lease.

## Synthetic Regression Evidence

Commands run on 2026-07-17 UTC from the task worktree:

```text
python3 scripts/test_status_command_runtime_pin.py
exit 0, 6 tests OK

python3 scripts/test_ai_status.py
exit 0, 74 tests OK

python3 .orchestrator/test_common.py
exit 0, 52 tests OK

python3 .orchestrator/test_worker_runner_heartbeat.py
exit 0, 13 tests OK

python3 .orchestrator/test_supervisor.py
exit 0, 277 tests OK

python3 .orchestrator/test_adapter_fallback_policy.py AdapterFallbackPolicyTests.test_codex_uses_request_workspace_and_status_root_metadata
exit 0, 1 test OK

python3 -m py_compile scripts/ai_status.py .orchestrator/common.py .orchestrator/supervisor.py .orchestrator/worker_runner.py scripts/test_status_command_runtime_pin.py
exit 0

bash -n scripts/ai-status.sh scripts/sync-dev-root.sh
exit 0

git diff --check
exit 0
```

Focused coverage added in `scripts/test_status_command_runtime_pin.py` proves:

- stale task-worktree wrapper executes the installed command runtime;
- direct Python from the stale worktree fails closed;
- a valid active worker lease can mutate central state while preserving the
  task worktree;
- missing worker, inactive worker, expired lease, wrong task, wrong workspace,
  wrong issued SHA, and missing worktree lease all fail before canonical
  writes;
- two concurrent worktree writers produce distinct activity event IDs and the
  final task state remains `review` after handoff, not an older active postimage.

## Pre-Pin Runtime Inventory

Read-only central runtime inspection on 2026-07-17 found seven worker records.
All inspected records had `has_status_command_runtime=false`, so they are
pre-pin leases and must be drained before installing the merged runtime.

Redacted tasks observed:

```text
OPS-LEASE-READ-AFTER-WRITE-PIN-001 suspended_approval
OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001 suspended_approval
LOOP-PROD-SEQ-RECONCILE-001 suspended_approval
OPS-STATUS-COMMAND-RUNTIME-PIN-001 running
OPS-RECONCILIATION-JSON-STORE-INTEGRITY-001 running
OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001 running
OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002 running
```

The current owner run was pre-pin:

```text
ORCH_RUN_ID=codex-20260717T041613Z-f2580225
ORCH_TASK_ID=OPS-STATUS-COMMAND-RUNTIME-PIN-001
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon
PANTHEON_WORKTREE_ROOT=/tmp/pantheon-worker-worktrees/pantheon/ops-status-command-runtime-pin-001
PANTHEON_COMMAND_ROOT was absent
PANTHEON_COMMAND_RUNTIME_SHA was absent
```

Running `AI_NAME=Codex2 ./scripts/ai-status.sh show
OPS-STATUS-COMMAND-RUNTIME-PIN-001` from this pre-pin worker failed closed with
`PANTHEON_COMMAND_ROOT is required for auto-worker status commands`.

## Postmerge Cutover Gate

Completion still requires these postmerge steps:

1. Stop new dispatch.
2. Drain or terminate only remaining pre-pin workers after safe handoff.
3. Install the exact reviewed merge SHA into the configured command root.
4. Verify command root remote is `ajoe734/pantheon`, `HEAD` equals the installed
   SHA, and that SHA is an ancestor of `origin/dev`.
5. Restart supervisor/watchdog from the installed root.
6. Issue new leases and verify every live worker record has
   `status_command_runtime.source_sha=<installed-merge-sha>`.
7. Re-run stale-worktree positive/negative proof under the new leases.
8. Re-run the dependent `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002` /
   PR #3763 proof after this pin is installed.

If any pre-pin worker remains able to mutate canonical status, keep canonical
mutations frozen and roll back the installed command root.
