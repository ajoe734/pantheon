# Task Brief: AG-CAND-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Complete Agora candidate provenance projection
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Chair reassigned review from Codex2 to Claude: Codex2 has two consecutive reviewer failures on this review task, including runs codex-20260722T183332Z-7494e3c0 and codex-20260722T194834Z-0dbcbc64; Claude is auth-ready, not dispatch-paused, and distinct from owner Codex.

## Summary
讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
