# Task Brief: SUP-RUNTIME-V10-MUTABLE-TRACKED-DRIFT-FOLLOWUP-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair mutable bootstrap handling of generated tracked drift
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Reject exact head 2213f74f: supervisor.run_once(watch=True) invokes watch_events._run_scan_locked, whose event loop still calls watch_events.queue_delivery_event; absent context_files there falls back to materializing common.execution_context_files and can dirty the mutable command checkout. Route the watcher queue path through the same pure context builder (or inject an equivalent non-materializing queue function), and add an end-to-end run_once/watch regression proving a missing tracked task brief is never written in the command root while isolated worktree materialization still occurs. Re-run promotion suite and focused/full affected supervisor tests; refresh the committed evidence manifest before re-review.

## Summary
The authorized 5877b644 rollout retry failed closed before mutation because bootstrap mutable-incumbent validation rejects one tracked task brief that the orchestrator itself regenerated in the active dev-root. Repair only the source boundary and regression fixtures; do not weaken governed launch-source identity or touch live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
