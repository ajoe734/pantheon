# Task Brief: OPS-DISPATCH-AUTHORITY-RECOVERY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden governed dispatch and retry recovery
- Status: todo
- Owner: Codex2
- Reviewer: Antigravity
- Next: Assignment created

## Summary
修正 Agora bulk dispatch 的 authoritative runtime／archive safety 與 GitHub explicit retry 的隔離 worktree 缺陷，避免再次污染 task journal 或落到 shared checkout。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
