# Task Brief: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote merged alias reassignment guard into live supervisor runtime
- Status: blocked
- Owner: Codex
- Reviewer: Claude
- Next: Exact-tip candidate 37ee6c5d is clean and regular discover-only gates pass except the expected legacy split-entrypoint pair. Bootstrap preparation then fails closed before admission/config/signal/launch because immutable rollback destination 0305c861 already exists at a different root/inode. Human/Ops or the supervisor must admit a source-only collision-safe rollback destination follow-up; do not delete, move, or guess-reuse the existing runtime.

## Summary
Supervisor-dispatched Antigravity runtime promotion only. Expected branch task/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731, clean governed worktree, merge target dev. Owner capability: supervisor runtime deployment and readback. Reviewer capability: independent Human/Ops exact-head/runtime evidence validation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
