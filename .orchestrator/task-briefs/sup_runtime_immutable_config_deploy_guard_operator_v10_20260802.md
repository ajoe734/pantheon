# Task Brief: SUP-RUNTIME-IMMUTABLE-CONFIG-DEPLOY-GUARD-OPERATOR-V10-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Keep supervisor deploy and sync paths on immutable command runtimes
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Last update: 2026-08-04T01:28:43Z
- Next: Independent review passed: PR #4526 remote head equals bd7039685e75c97cf18b35e984f97193a1c68e4d; all required Branch CI checks passed; reran 43 focused and 349 qualification tests successfully; verified immutable admission, config no-op/promotion handoff, split-root protection, source-only boundary, and committed evidence manifest.

## Summary
修正 provisioning、sync-dev-root 與 dev deploy，讓 supervisor 永久使用 SHA 命名 immutable command runtime，禁止之後再被改回 mutable dev-root。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
