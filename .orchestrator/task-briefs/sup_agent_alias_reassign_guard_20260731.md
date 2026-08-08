# Task Brief: SUP-AGENT-ALIAS-REASSIGN-GUARD-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Keep supervisor reassignments on canonical agent identities
- Status: review_approved
- Owner: Antigravity
- Reviewer: Human/Ops
- Next: Approved exact PR #4426 head 5fe69501eb8dbb6b98140c27fef1b0ca295bec96 after independent scope and behavior review. Changed only supervisor implementation, regression tests, and task brief; no config file change. Acceptance is covered end-to-end across both reassignment paths, canonical handoff/blocker identities, governed state persistence, and status-command usability. Verified 625 passed and 35 subtests passed; GitHub CI checks are green. Owner may perform governed closeout, protected merge, and live promotion.

## Summary
修正 supervisor fallback reassignment 使用 display_name 而非 canonical agent identity 的缺陷，避免 Copilot legacy label 汙染 authoritative task state。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
