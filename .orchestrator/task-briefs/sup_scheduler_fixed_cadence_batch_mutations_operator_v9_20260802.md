# Task Brief: SUP-SCHEDULER-FIXED-CADENCE-BATCH-MUTATIONS-OPERATOR-V9-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make supervisor cadence deadline-based and batch canonical/runtime mutations
- Status: in_progress
- Owner: Codex2
- Reviewer: Human/Ops
- Next: Exact-head fb923abe rejected: launch recovery lacks post-intent generation binding. Independent fake-/proc reproduction proves both a worker_runner candidate whose generation necessarily predates a future prepared_epoch_seconds and a marker started in 2020 with a freshly updated heartbeat mtime are accepted as run-old. _runtime_launch_process_candidates never compares process start epoch to the intent, and _runtime_launch_marker_candidates filters mtime rather than marker started_at; recovery can therefore adopt an older same-task/agent run and bind it to the new queue_event_id. Add end-to-end temporal proof (PID start epoch from btime+ticks or exact launch token), prevent unique-marker fallback from bypassing it, and add negative pre-intent process plus fresh-heartbeat-marker tests while retaining post-intent adoption and distinct Codex/Codex2 groups.

## Summary
Remove full-sleep drift and per-task mutation convoys so dispatch cadence tracks its deadline while preserving fail-closed governance.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Latest Exact-Head Follow-Up

- Rejected head: `ba3b176500f557944eb6f3e08709361281609989`.
- Human/Ops found that flooring fractional `prepared_epoch_seconds` still let same-second runner markers claim post-intent order, including marker-only terminal recovery, and that a unique live marker could bypass an absent exact process candidate.
- Remediation anchor: `267bbc09fcbd51e0d86787fb34b51b773c569d8e`. Marker timestamps now compare against the full intent epoch; same-second ambiguity fails closed; running/live markers require an exact post-intent `/proc` candidate; strictly post-intent terminal/dead markers remain recoverable.
