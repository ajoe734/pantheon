# OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001 Evidence

Date: 2026-07-16
Owner: Codex2
Reviewer: Antigravity

## Delivered Behavior

- Auto workers may continue running product commands from the isolated task
  worktree.
- Governed status commands now require an explicit `PANTHEON_STATUS_ROOT` when
  auto-worker environment markers are present.
- `PANTHEON_STATUS_ROOT` must be absolute, must exist, must be a git repository
  root, must contain `ai-status.json`, and must not include any symlink
  component.
- Auto workers compare `PANTHEON_STATUS_ROOT` with the supervisor runtime paths
  in `ORCH_RUNNER_STATUS_PATH` and `ORCH_HEARTBEAT_PATH`, so a second valid repo
  with its own `ai-status.json` is rejected.
- `PANTHEON_STATUS_ROOT` is rejected when it points at the isolated task
  worktree itself.
- `scripts/ai_status.py` binds status, activity, current-work, dashboard,
  archive, and lock paths to one validated root and fails if any binding escapes
  that root or is symlinked.
- `.orchestrator/worker_runner.py` validates the same root before launching the
  provider child process, then preserves child cwd isolation in the task
  worktree.
- Generated worker task briefs tell workers to use `./scripts/ai-status.sh`
  normally; the supervisor-supplied env routes coordination writes.

## Regression Coverage

The new regression tests use temporary git repositories for a central
coordination root and a task worktree. They prove that `show`, `progress`,
`handoff`, reviewer `reopen`, reviewer `approve`, and owner `done` run from the
task worktree but read/write only central `ai-status.json`,
`ai-activity-log.jsonl`, archive, derived output, and lock paths. The stale
worktree `ai-status.json`, activity log, archive file, derived files, and lock
sidecars remain byte-identical.

Invalid bindings covered:

- missing `PANTHEON_STATUS_ROOT` in an auto-worker environment
- relative `PANTHEON_STATUS_ROOT`
- missing root
- symlinked root or symlinked path component
- root equal to the isolated task worktree
- a second valid git repo with its own `ai-status.json` when the supervisor
  runtime paths identify a different root
- non-git repository root

## Verification

Passed:

```bash
env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH \
  python3 -m py_compile scripts/ai_status.py .orchestrator/worker_runner.py .orchestrator/supervisor.py scripts/test_ai_status.py .orchestrator/test_worker_runner_heartbeat.py .orchestrator/test_supervisor.py .orchestrator/test_common.py

env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH \
  python3 -m pytest scripts/test_ai_status.py
# 74 passed

env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_worker_runner_heartbeat.py .orchestrator/test_runtime_state.py
# 49 passed

env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_common.py
# 34 passed

env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_prepare_worker_workspace_allocates_task_worktree_metadata .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_generated_worker_task_brief_mentions_inherited_status_root
# 2 passed

env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_adapter_fallback_policy.py .orchestrator/test_supervisor_watchdog.py scripts/test_supervisor_watchdog_install.py
# 45 passed

git diff --check
# no output
```

Full supervisor suite result on this branch:

```bash
env -u PANTHEON_STATUS_ROOT -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUN_ID -u ORCH_TASK_ID -u AI_NAME -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONPATH=.orchestrator \
  python3 -m pytest .orchestrator/test_supervisor.py
# 272 passed, 4 failed
```

The four failures are existing `.orchestrator/config.json` expectation
mismatches outside this task's touched behavior:

- `RuntimeConfigTests.test_claude_concurrency_is_explicitly_capped_at_three`
  expects `ready_dispatcher.max_tasks_per_agent_by_agent.Claude == 3`, observed
  `0`.
- `RuntimeConfigTests.test_claude2_new_work_target_and_concurrency_are_capped`
  expects `ready_dispatcher.target_workload.Claude2 == 5`, observed `0`.
- `RuntimeConfigTests.test_antigravity_workers_are_in_dispatcher_pool` expects
  `ready_dispatcher.target_workload.Antigravity == 5`, observed `0`.
- `UnderutilizationSidecarDispatchTests.test_unregistered_runtime_config_agent_is_not_eligible_for_sidecars`
  expects `["Codex", "Claude", "Gemini"]`, observed `["Codex", "Gemini"]`.

This task does not modify `.orchestrator/config.json` or sidecar eligibility
policy.
The same four selected tests also fail on `origin/dev` baseline commit
`d55a0caf7` with the same observed values.
