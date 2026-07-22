# Task Brief: OPS-PROMOTE-CONFLICT-RECOVERY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Recover publish-to-master promote train
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Reproduce v2026.07.15.0 conflict and make candidate handling deterministic.

## Summary
修復 publish-promote 在第一個 historical conflict 即中止的行為，逐筆分類候選並保留 protected checks、tag immutability 與 rollback safety。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
