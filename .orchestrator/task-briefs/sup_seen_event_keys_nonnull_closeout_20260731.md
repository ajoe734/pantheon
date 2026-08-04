# Task Brief: SUP-SEEN-EVENT-KEYS-NONNULL-CLOSEOUT-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add merged watcher terminal reconciliation evidence
- Status: todo
- Owner: Antigravity
- Reviewer: Human/Ops
- Next: Create only the missing merged evidence needed to reconcile SUP-SEEN-EVENT-KEYS-NONNULL-20260731. Use a clean task branch and PR; do not edit implementation or config. The tracked Markdown evidence must have exact lines # Task Brief: SUP-SEEN-EVENT-KEYS-NONNULL-20260731, - Status: review_approved, - Owner: Antigravity, - Reviewer: Human/Ops, cite repository ajoe734/pantheon and full delivery commit fd67904e2c1adb7256d4d9d9dc618105346be424, and record watcher regression plus live absence of run_scan/trim_seen_events TypeError.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
