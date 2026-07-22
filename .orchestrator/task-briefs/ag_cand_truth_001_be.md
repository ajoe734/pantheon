# Task Brief: AG-CAND-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Complete Agora candidate provenance projection
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Auto-reassigned ownership from Claude to Codex after repeated Claude terminal: Worker exited before the task reached a terminal status.. Task returned to todo until Codex starts a fresh run.

## Summary
讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
