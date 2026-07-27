# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Codex anchored the owner rescue as 09af22e3c05ebea666f65ee34f57862cfc265840, merged origin/dev 87166a352c0b90a26a6e35c138acfaea195fa4ee through 8f4731aa86cbe99da6b535fa565a1dcb84474c40, and revalidated the REST repair. Publish the updated PR #4262 head for Claude independent review and the governed external merge gates.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
