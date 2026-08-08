# Task Brief: SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Independently review scheduler eligibility and grace exact head for live rescue
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Non-rewrite replacement packet `pkt-sup-preemption-review-evidence-r2-20260808T1512Z` was admitted and materialized as `SUP-PREEMPTION-REVIEW-EVIDENCE-R2-20260808`. Wait for the supervisor-dispatched replacement branch and PR; Codex2 must independently review its exact head before merge, and PR #4402 remains preserved until that replacement lands.

## Summary
Emergency independent review lane for the already implemented #4399 scheduler architecture fix. Human/Ops hands this review-only task to Antigravity so the true supervisor launches the reviewer without waiting on the closeout task that the old scheduler itself keeps killing.

## Non-Rewrite Replacement Dispatch (2026-08-08)

- PR #4402 exact head `17d5665f57c302d71f522f9a6e9b0f95c77473e3`
  records the provider-readiness counterexample, but its ancestry contains
  `aae0e2cb4da0a0ae9f049bbdf166c13f2c09daee`, whose 74-character subject
  fails the required Commit trailers job. An appended commit cannot remove
  that pushed ancestor from the CI range.
- The current auto-worker lease correctly rejected a direct cross-task
  `assign`; no canonical state was mutated through that refused command.
- The governed assistant dev bridge then accepted packet
  `pkt-sup-preemption-review-evidence-r2-20260808T1512Z` and produced a
  supervisor receipt at `2026-08-08T15:07:54Z`. Its authoritative
  materialization readback created
  `SUP-PREEMPTION-REVIEW-EVIDENCE-R2-20260808` with owner `Codex`, reviewer
  `Codex2`, and expected branch
  `task/SUP-PREEMPTION-REVIEW-EVIDENCE-R2-20260808`.
- The replacement must preserve PR #4402 and its branch/history without amend,
  force push, deletion, or ref recreation. It must not modify PR #4399,
  `.orchestrator/config.json`, runtime state, or generated collaboration state.
- Publication order is replacement PR, Codex2 exact-head review, governed
  merge to `dev`, then closure of PR #4402 as superseded. Rollback is a revert
  of the replacement merge.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
