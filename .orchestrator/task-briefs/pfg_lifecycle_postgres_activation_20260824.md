# Task Brief: PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Activate the current-dev PostgreSQL Lifecycle writer and reader
- Owner: Antigravity
- Reviewer: Codex2
- Status: todo
- Next: After PFG-FUNCTIONAL-REAUDIT-DOCS-20260824 merges; implement the Lifecycle core lane from the corrected SD. Keep deployment switching and exact JSON cleanup for LIFECYCLE-PROJ-RETIRE-001.

## Summary
修復 current dev 仍停在 JSON reader 且 PostgreSQL projection 為零的實際 regression；只負責 relational activation。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
