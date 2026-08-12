# Task Brief: SUP-RUNTIME-V10-ENOTDIR-EVIDENCE-CLOSEOUT-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Correct ENOTDIR review evidence and close merged delivery
- Owner: Claude
- Reviewer: Codex
- Status: todo
- Next: Helper-claimed by Claude while Codex is dispatch-paused previous owner Codex becomes reviewer.

## Summary
Repair only the committed evidence truth for the already-reviewed and merged ENOTDIR source change. Bind the original canonical Codex2 approval, PR #4642 exact head, protected merge, and validation in a fresh correction PR whose own evidence is present before review. Do not change promotion code or live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
