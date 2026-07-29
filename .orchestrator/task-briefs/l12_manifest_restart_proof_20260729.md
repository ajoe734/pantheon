# Task Brief: L12-MANIFEST-RESTART-PROOF-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest isolated restart proof workstream
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Auto-reassigned ownership from Codex2 to Codex after repeated Codex2 terminal: fatal: ambiguous argument 'origin/task/L12-MANIFEST-RESTART-PROOF-20260729': unknown revision or path not in the working tree.

## Summary
補 isolated/non-shared PID1 crash restart proof，或取得明確 governed waiver。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
