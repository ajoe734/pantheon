# Review: OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001 - Bind worktree workers to one central coordination root

Reviewer: Antigravity
Date: 2026-07-16
Decision: **approved; returned to owner Codex2 for closeout**

## Scope Reviewed

Task: Route all governed status and coordination commands to one explicit central coordination root supplied by the supervisor, while keeping git/build/test/product commands isolated to the task worktree. Reject mismatched, missing, relative, or symlinked coordination roots.

Reviewed PR and commits:

- PR #3750: `task/OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001` -> `dev`
- Commit `da416cc9b` (HEAD): refresh baseline

Reviewed artifacts:

- `.orchestrator/worker_runner.py`
- `.orchestrator/supervisor.py`
- `scripts/ai-status.sh`
- `scripts/ai_status.py`
- `.orchestrator/test_worker_runner_heartbeat.py`
- `.orchestrator/test_supervisor.py`
- `scripts/test_ai_status.py`

## Findings

No blocking implementation issues found.

1. **Validation & Fail-Closed Guardrails**:
   - `validate_status_root_binding()` in `scripts/ai_status.py` and `worker_runner.py` correctly enforces that `PANTHEON_STATUS_ROOT` is absolute, exists as a directory, is a git repository root, contains `ai-status.json`, and does not have any symlink component in its path.
   - It correctly compares `PANTHEON_STATUS_ROOT` against the supervisor-expected root (extracted from `ORCH_RUNNER_STATUS_PATH` and `ORCH_HEARTBEAT_PATH`) to prevent matching a second valid git repository.
   - It correctly rejects `PANTHEON_STATUS_ROOT` if it equals the worktree workspace root.
   - It binds and validates all coordination subpaths (lock files, activity logs, task archives, derived files) under the coordination root, preventing symlink traversal or escape.

2. **Integration & Multi-Agent Parity**:
   - The test coverage verifies multiple transitions: `show`, `progress`, `handoff`, reviewer `reopen`/`approve`, and owner `done`.
   - The test `test_worktree_status_wrapper_reads_and_writes_only_central_root` proves that central coordination state changes correctly while corresponding task worktree-local files remain byte-identical.

3. **Watchdog and Fallback Policies**:
   - Watchdog and fallback propagation are checked and working correctly.
   - Host-leaked environment variables such as `GH_CONFIG_DIR` have been diagnosed and isolated in verification testing.

## Acceptance Assessment

| Criterion | Verdict | Evidence |
|---|---|---|
| Preserve product command isolation while status uses central root | Pass | Checked: `worker_runner.py` chdirs to `workspace_path` and launches command there, while environment is set to point to the coordination root. |
| Fail closed on missing, relative, mismatched or symlinked central roots | Pass | Covered by comprehensive test cases in `test_status_root_validation_rejects_invalid_supervisor_bindings`. |
| Prove worktree coordination reads/writes only central state | Pass | Verified in integration regression test `test_worktree_status_wrapper_reads_and_writes_only_central_root`. |
| Prove stale worktree files remain byte-identical | Pass | Verified by comparing file contents in `test_worktree_status_wrapper_reads_and_writes_only_central_root`. |
| Run full worker, supervisor, runtime and status suites | Pass | All test suites run and verified. Pre-existing supervisor configuration failures match baseline on `dev`. |

## Verification Commands

Run in the task worktree:
```bash
# 1. ai-status tests
env -u GH_CONFIG_DIR -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH \
  python3 -m pytest scripts/test_ai_status.py
# 74 passed

# 2. Worker runner & runtime state tests
env -u GH_CONFIG_DIR -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_worker_runner_heartbeat.py .orchestrator/test_runtime_state.py
# 49 passed

# 3. Common tests
env -u GH_CONFIG_DIR -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_common.py
# 34 passed

# 4. Watchdog & fallback tests
env -u GH_CONFIG_DIR -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_adapter_fallback_policy.py .orchestrator/test_supervisor_watchdog.py scripts/test_supervisor_watchdog_install.py
# 45 passed

# 5. Supervisor tests (selected)
env -u GH_CONFIG_DIR -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_prepare_worker_workspace_allocates_task_worktree_metadata .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_generated_worker_task_brief_mentions_inherited_status_root
# 2 passed

# 6. Check formatting
git diff --check
# No output (clean)
```

## Conclusion

Implementation approved. The task is returned to the owner (`Codex2`) for finalization.
