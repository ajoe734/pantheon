# Task Brief: L12-MANIFEST-RESTART-PROOF-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest isolated restart proof workstream
- Status: todo
- Owner: Codex2
- Reviewer: Claude2
- Next: Workstream under L12-MANIFEST-001. Do not edit .orchestrator/config.json. Produce isolated non-shared restart proof with RestartCount increment, or a governed waiver packet, for L12-MANIFEST-001 evidence.

## Summary
補 isolated/non-shared PID1 crash restart proof，或取得明確 governed waiver。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
