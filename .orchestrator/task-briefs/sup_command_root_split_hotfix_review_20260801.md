# Task Brief: SUP-COMMAND-ROOT-SPLIT-HOTFIX-REVIEW-20260801

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Independently review and merge supervisor command-root split hotfix
- Status: todo
- Owner: Codex
- Reviewer: Antigravity
- Next: Operator corrected review ownership after automatic Human/Ops-to-Codex reviewer reassignment: Codex owns the prepared delivery; Antigravity is the required independent exact-head reviewer. Signed scope, acceptance, and bridge provenance remain unchanged.

## Summary
Independent exact-head review and merge handoff for the bounded permanent repair after the temporary live rescue restored trusted dev-root worker dispatch.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
