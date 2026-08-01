# Task Brief: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote merged alias reassignment guard into live supervisor runtime
- Status: in_progress
- Owner: Codex2
- Reviewer: Antigravity
- Next: Codex2 revalidated live state without mutation. The sole supervisor is PID 2933948 at cbb36ff1, not target 012dab969; the prior candidate root has tracked dirty state; one active worker record referenced a dead PID; and the governed rollback transaction replacement chain is incomplete. Evidence is anchored under docs/deployment/evidence/supervisor/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731/. Block live mutation until lease parity is safe and SUP-RUNTIME-PROMOTION-ROLLBACK-TRANSACTION-20260801 plus its failure-matrix integration are merged and available.

## Summary
Supervisor-dispatched Antigravity runtime promotion only. Expected branch task/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731, clean governed worktree, merge target dev. Owner capability: supervisor runtime deployment and readback. Reviewer capability: independent Human/Ops exact-head/runtime evidence validation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
