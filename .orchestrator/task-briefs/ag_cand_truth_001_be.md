# Task Brief: AG-CAND-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Complete Agora candidate provenance projection
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Reviewer fixes are anchored through `eda9747f0`; merge current `origin/dev`, rerun the complete candidate/research/v1.10 regression, and return the pushed branch to Codex2 for re-review.

## Summary
讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
