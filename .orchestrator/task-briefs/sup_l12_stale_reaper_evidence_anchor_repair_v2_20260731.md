# Task Brief: SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Supersede Wave0X #4385 evidence-anchor repair with current-head spec
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Owner Claude re-verified the delivery at the current head and rebound the evidence manifest to the post-reassignment owner/reviewer pair, then handed off to Antigravity for independent exact-head review of PR #4465. The four earlier reopen rounds were issued by Claude as reviewer before the 2026-08-06T15:56Z reassignment, so they are recorded as prior reviews and none of them counts as the independent review of the delivered head.

## Summary
Supersedes preempted immutable task SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731 after bridge rejected spec update. Repair #4385 nonexistent evidence anchor before stale-reaper can satisfy Wave 0.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
