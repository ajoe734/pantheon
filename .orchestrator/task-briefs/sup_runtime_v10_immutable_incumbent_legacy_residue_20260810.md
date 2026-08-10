# Task Brief: SUP-RUNTIME-V10-IMMUTABLE-INCUMBENT-LEGACY-RESIDUE-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind historical task-brief residue on immutable incumbent promotion
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Human/Ops canonical recovery of Codex2 rejection: source scope and focused/full tests are acceptable, but PR #4716 is BLOCKED because commits 9dd5d1f11 and ea7dfe311 have 76-character subjects over the 72-character trailer gate. Supervisor must dispatch Antigravity2 to publish a clean task PR history with every origin/dev..HEAD commit passing trailers, preserve the reviewed evidence manifest, wait for checks, then request fresh Codex2 exact-head review. No live promotion.

## Summary
The authorized 963bb20c retry reached a clean candidate but treated the accepted SHA-named incumbent as a normal immutable root, so the existing mutable-only legacy bootstrap was not enabled and one pre-fix generated task-brief overwrite aborted the transaction. Extend only the explicit provenance-bound incumbent bootstrap boundary; preserve strict candidate cleanliness and all transaction gates.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
