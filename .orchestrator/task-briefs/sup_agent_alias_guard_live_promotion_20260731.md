# Task Brief: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote merged alias reassignment guard into live supervisor runtime
- Status: blocked
- Owner: Codex
- Reviewer: Claude
- Next: Split-entrypoint support merged through PR #4724 at 8b77e779, but read-only bootstrap preparation on clean candidate 0305c861 aborts before admission lock, config, signal, or launch because rollback materialization aliases the candidate root (`Candidate runtime equals the rollback runtime`). Active-lease governance correctly rejected cross-task creation by this worker. Human/Ops or the supervisor must admit a narrow source-only non-alias rollback follow-up before any governed promotion retry.

## Summary
Supervisor-dispatched Antigravity runtime promotion only. Expected branch task/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731, clean governed worktree, merge target dev. Owner capability: supervisor runtime deployment and readback. Reviewer capability: independent Human/Ops exact-head/runtime evidence validation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
