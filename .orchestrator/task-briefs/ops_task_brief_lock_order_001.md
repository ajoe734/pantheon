# Task Brief: OPS-TASK-BRIEF-LOCK-ORDER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix nested task-state lock during worker brief generation
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Safety revalidation for merged PR #4197 is complete on exact merge commit `e82371783e18c4bac7b0c2ca650c0904a8c004f3`; submit the isolated proof below to Codex2 for independent review.

## Summary
修復 supervisor 在已持有 runtime_admission/task_state 鎖時再次取得 task_state，造成完整 task brief 生成失敗並退回 minimal context。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Implementation Evidence
- `write_task_brief` reads the active task and archived dependencies under the configured canonical task-state lock, with `TaskResolver` bound to that same status root.
- The run_once/process_queue regression trace records exactly one real `task_state` acquisition after `runtime_admission`; full task fields, archived dependency status, and artifacts render without the minimal-context fallback.
- The implementation is merged to `dev` through PR #4197 at `e82371783e18c4bac7b0c2ca650c0904a8c004f3`.

## Isolated Safety Revalidation
- Revalidation ran in detached worktree `/tmp/ops-task-brief-lock-order-verify-OxFPa8/repo` at the exact PR merge commit.
- The focused regression and full supervisor suite ran under `env -i` with `PANTHEON_STATUS_ROOT=/tmp/ops-task-brief-lock-order-verify-OxFPa8/status-root`, `PANTHEON_TASK_STATE_STORE_MODE=authoritative`, and `PANTHEON_TASK_STATE_EVENT_LOG=/tmp/ops-task-brief-lock-order-verify-OxFPa8/task-state-events.jsonl`: focused 1/1 and `test_supervisor` 334/334 passed.
- `test_common` ran again under `env -i` with no `PANTHEON_*` variables and passed 90/90. A preceding diagnostic run with the isolated authoritative variables intentionally changed one empty-status error path to the authoritative empty-journal error; it recorded its only task-state event at isolated-journal sequence 1 and did not touch live state.
- All four test process trees were traced with `strace -f -P /home/lupin/pantheon-ci-deploy/runtime/task-state-events.jsonl -e trace=%file`. The live journal path had zero syscall matches in `focused`, `supervisor`, `common`, and `common-clean` traces.
- Live journal snapshots moved from sequence 1705 to 1708 during the isolated suites and from 1710 to 1712 during clean-env `test_common`, solely through concurrent `antigravity1-*` sources. Sequences 1706-1712 each retained 23 tasks; no test process opened or appended the live journal.
