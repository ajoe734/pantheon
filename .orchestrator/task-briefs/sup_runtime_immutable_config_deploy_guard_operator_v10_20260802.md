# Task Brief: SUP-RUNTIME-IMMUTABLE-CONFIG-DEPLOY-GUARD-OPERATOR-V10-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Keep supervisor deploy and sync paths on immutable command runtimes
- Status: todo
- Owner: Codex2
- Reviewer: Human/Ops
- Next: Wait for atomic bootstrap transaction source task then make deploy and sync paths compose with it; source-only until the V9 canary rollout.

## Summary
修正 provisioning、sync-dev-root 與 dev deploy，讓 supervisor 永久使用 SHA 命名 immutable command runtime，禁止之後再被改回 mutable dev-root。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
