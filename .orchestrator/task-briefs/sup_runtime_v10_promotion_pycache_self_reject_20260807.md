# Task Brief: SUP-RUNTIME-V10-PROMOTION-PYCACHE-SELF-REJECT-20260807

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: governed promotion pipeline rejects its own generated __pycache__, blocking all automated dev-root sync
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Next: Governed operator transition from stranded ready into the supervisor-owned in_progress scan set. Preserve owner Codex and reviewer Claude2; repair automated dev-root promotion self-rejection, then promote merged PRs #4582 and #4625.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
