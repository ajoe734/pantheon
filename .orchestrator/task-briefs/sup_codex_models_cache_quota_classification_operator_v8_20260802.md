# Task Brief: SUP-CODEX-MODELS-CACHE-QUOTA-CLASSIFICATION-OPERATOR-V8-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Recover incompatible Codex model cache and classify usage-limit quota accurately
- Status: todo
- Owner: Codex2
- Reviewer: Human/Ops
- Next: Assignment created

## Summary
Make one provider home self-recover from an exactly identified incompatible models cache, then classify actual usage-limit evidence as quota and reassign only affected tasks across still-distinct configured groups.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
