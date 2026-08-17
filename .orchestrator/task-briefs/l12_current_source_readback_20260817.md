# Task Brief: L12-CURRENT-SOURCE-READBACK-20260817

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix Source Ingestion durable terminal readback gap
- Owner: Claude
- Reviewer: Antigravity
- Status: todo
- Next: Recovery reassigned owner from Codex after durable terminal_quota:codex1-1; planner will redispatch normally.

## Summary
研究鏈 E2E 卡在 source.durable_terminal_readback：POST /api/source-ingest/jobs 對 static_records 連接器回 201，但 SourceRecord 從未出現在讀回端點。診斷 scheduler.run_once／configured fetch 持久化／讀寫 store 是否不一致並修復，讓預設 pantheon compose 上的部署 E2E 真的走完 Loop1-4。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
