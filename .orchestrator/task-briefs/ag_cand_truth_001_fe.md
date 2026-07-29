# Task Brief: AG-CAND-TRUTH-001-FE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Stop mixing live candidates with sample fields
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Owner closeout verified execute-plans PR #506 at reviewed HEAD f9fb01d6 and merged it into dev as 9597d0c3. Pantheon review and closeout records are ready for the task PR; after that PR merges into Pantheon dev, finalize with the governed done command and delivery metadata.

## Summary
移除 live candidate + DEFAULT_CANDIDATES 混合；每欄只顯示同一 identity 的真實值、明確 unknown/stale，或整張清楚標示 sample。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
