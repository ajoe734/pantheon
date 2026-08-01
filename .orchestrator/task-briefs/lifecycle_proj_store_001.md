# Task Brief: LIFECYCLE-PROJ-STORE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Build the lifecycle projection relational store
- Status: in_progress
- Owner: Codex
- Reviewer: Antigravity
- Next: Owner repairs and checksummed real-PostgreSQL evidence are ready on the task branch; open the PR and hand the exact green head to Antigravity for independent review.

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-STORE-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner repair checkpoint

- Database invariant anchor: `e46308f432bc38ac8e3d65c8f8405a1882aea544`.
- Focused real-PostgreSQL result: 17 passed.
- Adjacent Trade Journey regression: 136 passed.
- Review manifest: `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-STORE-001/evidence.json`.
- Review must bind the exact PR head and record the independent decision in the checksummed manifest before approval.
