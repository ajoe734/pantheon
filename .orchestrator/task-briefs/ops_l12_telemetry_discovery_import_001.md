# Task Brief: OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Eliminate telemetry unittest discovery loader errors
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Claude owns the canonical lane; Human/Ops corrected the evidence owner binding after functional review passed and is preparing the repair PR for Codex2 independent review.

## Summary
修正 telemetry 完整 unittest discovery 的兩個裸模組 import error，讓乾淨 repo-root 與 package discovery 都能零 loader error。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
