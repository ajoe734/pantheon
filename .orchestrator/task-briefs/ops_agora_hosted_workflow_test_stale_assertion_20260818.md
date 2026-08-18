# Task Brief: OPS-AGORA-HOSTED-WORKFLOW-TEST-STALE-ASSERTION-20260818

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix stale exact-head assertion in the hosted-acceptance probe test
- Owner: Claude
- Reviewer: Antigravity2
- Status: query the governed `ai-status.sh show` command; do not transcribe it into this file.
- Next: close out only the already-reviewed delivery; do not commit this generated brief as an approval record.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
