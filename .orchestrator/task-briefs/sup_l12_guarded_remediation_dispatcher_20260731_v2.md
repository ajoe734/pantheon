# Task Brief: SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731-V2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Extend the program-specific guarded dispatcher for current-proof remediation (V2 delivery identity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Human/Ops
- Next: Exact content and tests pass, but the latest commit subject and Task-ID trailer still name SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731. Add one empty task-scoped closeout commit whose subject includes SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731-V2 and trailers bind LLM-Agent: Antigravity, Task-ID: SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731-V2, Reviewer: Human/Ops; do not change the seven-file tree, config, catalog, or product tasks. Push #4417, wait CI, update exact source/review binding, then hand off.

## Summary
以符合 canonical branch/task identity 的 V2 任務承接已完成的 guarded dispatcher 實作與 PR #4417 exact-head 審查；不改寫 28-task catalog。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
