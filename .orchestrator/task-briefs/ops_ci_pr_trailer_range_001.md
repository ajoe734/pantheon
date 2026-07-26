# Task Brief: OPS-CI-PR-TRAILER-RANGE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Scope PR commit-trailer CI to the exact task head
- Status: todo
- Owner: Claude
- Reviewer: Codex2
- Next: Repair the Branch CI pull_request range contract. Current failed run 30219467575 scans 03389c0..synthetic merge 0942107 and rejects dev commit 0410a89f0 even though it is not owned by PR #4211; #4215 shows the same contamination. Test exact stale-base and concurrent-dev-advance shapes. Do not edit config or weaken trailer validation.

## Summary
修正 PR trailer gate 掃到 integration base 與 synthetic merge commit 的錯誤範圍，避免別人的已合併 commit 阻塞所有 task PR。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
