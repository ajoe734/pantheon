# Task Brief: OPS-TASK-PR-TRIAGE-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Architecture-level fleet recovery: after OPS-PR-REVIEW-BEFORE-MERGE-GATE-001 lands, drain stale task PR backlog using active Codex2/Codex lanes instead of unavailable Claude. Keep stale-PR classification evidence-based and do not close or merge PRs without task-state proof.
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Auto-reassigned OPS-TASK-PR-TRIAGE-002 away from unavailable lane Codex (disabled, paused, sidecar-only, or auth-down); reviewer Codex -> Claude.

## Summary
盤點並治理 stale task PR，避免 fleet throughput 被舊 PR、失效 review、或無證據 closeout 卡住。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
