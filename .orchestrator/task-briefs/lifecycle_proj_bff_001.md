# Task Brief: LIFECYCLE-PROJ-BFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Serve Trade Journey and loop-run reads from Postgres
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Review cannot approve: the merged evidence manifest docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-BFF-001/evidence.json still records review_decision=pending_independent_review and stale owner/reviewer Codex/Claude, while canonical task ownership is Antigravity/Codex2. Publish a new task PR whose pre-review manifest contains the required independent review decision and current task identities, then return it to review for a fresh exact-head approval; do not alter reader cutover/default-disabled behavior.

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-BFF-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
