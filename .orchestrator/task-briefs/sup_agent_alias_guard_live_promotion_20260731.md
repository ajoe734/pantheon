# Task Brief: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote merged alias reassignment guard into live supervisor runtime
- Status: blocked
- Owner: Codex
- Reviewer: Claude
- Next: Read-only promotion preflight proves the clean 0305c861 candidate contains the alias guard and passes projection, lease, provider, and candidate-identity gates, but live PID 1393542 uses split cwd and argv entrypoint roots. Wait for independently reviewed and merged SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810, then rerun discover-only before any governed promotion retry.

## Summary
Supervisor-dispatched Antigravity runtime promotion only. Expected branch task/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731, clean governed worktree, merge target dev. Owner capability: supervisor runtime deployment and readback. Reviewer capability: independent Human/Ops exact-head/runtime evidence validation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
