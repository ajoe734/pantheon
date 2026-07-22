# Task Brief: OPS-PROMOTE-CONFLICT-RECOVERY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Recover publish-to-master promote train
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Review approved: deterministic candidate dispositions, exact-snapshot rollback-safe bridge, protected auto-merge terminal at ff77abecf verified (master tree == release tree, tags immutable), idempotency run 29945824590 green, 7 stale promote PRs reconciled, 34/34 focused tests re-run by reviewer. Returned to Codex2 for finalization.

## Summary
修復 publish-promote 在第一個 historical conflict 即中止的行為，逐筆分類候選並保留 protected checks、tag immutability 與 rollback safety。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
