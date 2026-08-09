# Task Brief: SUP-RUNTIME-V10-LEGACY-CONTEXT-CANONICALITY-FOLLOWUP-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind historical generated task-brief canonicality for legacy bootstrap
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Antigravity
- Next: Replace ecdc structural authorization with candidate-tracked exact provenance bindings: path/task/length/SHA256 + authoritative historical event + old command-runtime 5877b644 + prevention boundary f5570754. Never trust mutable disk status/archive. Keep current-render/head-blob fast paths and identity revalidation; add mutated-byte/wrong-path/wrong-event/source/boundary negatives; update evidence, amend or add compliant commit, push, test, handoff.

## Summary
PR #4667 retained strict byte equality by re-rendering legacy brief content from current bound config, but the active pre-fix brief now fails that comparison before any promotion mutation. Repair only a fail-closed historical-context provenance boundary for the known generated residue; do not broaden drift acceptance or touch the live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
