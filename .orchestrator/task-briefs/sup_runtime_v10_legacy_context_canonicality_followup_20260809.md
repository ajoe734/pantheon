# Task Brief: SUP-RUNTIME-V10-LEGACY-CONTEXT-CANONICALITY-FOLLOWUP-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind historical generated task-brief canonicality for legacy bootstrap
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Reject PR #4671 head 9652c6aac: its provenance constant contains only a 634-byte test fixture and omits the two actual live dev-root drifts (1364/21a8c81a... and 2132/40bb9032...). Neither live blob exists in candidate Git history, so real rollout still fails closed. Add exact path/task/length/SHA plus authoritative event, old command-runtime 5877b644, and prevention-boundary f5570754 bindings for both; add positive live-byte and wrong event/source/boundary negatives; update evidence and request fresh exact-head review.

## Summary
PR #4667 retained strict byte equality by re-rendering legacy brief content from current bound config, but the active pre-fix brief now fails that comparison before any promotion mutation. Repair only a fail-closed historical-context provenance boundary for the known generated residue; do not broaden drift acceptance or touch the live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
