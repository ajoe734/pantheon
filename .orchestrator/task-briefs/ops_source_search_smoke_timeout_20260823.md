# Task Brief: OPS-SOURCE-SEARCH-SMOKE-TIMEOUT-20260823

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make persistent source-search bounded smoke finish within measured timeout
- Owner: Codex
- Reviewer: Antigravity2
- Status: todo
- Next: Regression evidence: dev deploy run 32648522828 source-search-bounded-smoke reached source-ingest and search readiness then timed out against persistent dev state. Follow up OPS-DEV-ROOT-SMOKE-IDEMPOTENCY-001 without reopening its terminal fact; preserve manual-only Source Ingestion.

## Summary
修正 persistent dev state 下 source-search-bounded-smoke timeout，維持手動單次資料拉取政策與 bounded egress，不恢復週期性外拉。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
