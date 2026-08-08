# Task Brief: SUP-RUNTIME-V10-MUTABLE-TRACKED-DRIFT-FOLLOWUP-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair mutable bootstrap handling of generated tracked drift
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Helper-claimed by idle Codex previous owner Codex2 becomes reviewer.

## Summary
The authorized 5877b644 rollout retry failed closed before mutation because bootstrap mutable-incumbent validation rejects one tracked task brief that the orchestrator itself regenerated in the active dev-root. Repair only the source boundary and regression fixtures; do not weaken governed launch-source identity or touch live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Declared Scope
scope:
  - .orchestrator/supervisor.py
  - .orchestrator/test_supervisor.py
  - .orchestrator/task-briefs/sup_runtime_v10_mutable_tracked_drift_followup_20260808.md
  - scripts/test_promote_supervisor_runtime.py
  - docs/deployment/evidence/supervisor/SUP-RUNTIME-V10-MUTABLE-TRACKED-DRIFT-FOLLOWUP-20260808
