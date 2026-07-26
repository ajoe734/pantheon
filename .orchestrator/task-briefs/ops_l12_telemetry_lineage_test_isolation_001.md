# Task Brief: OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Isolate telemetry lineage full-stack test from ambient runtime-manager configuration
- Status: todo
- Owner: Claude
- Reviewer: Antigravity
- Next: Supervisor dispatches Claude auto worker; implement only test-owned fixture/isolation and evidence. Do not change live config or weaken RuntimeManagerClient fail-closed defaults. Antigravity reviews when provider lane is available.

## Summary
修正 telemetry lineage full-stack 測試對 ambient PANTHEON_RUNTIME_MANAGER_URL 的隱性依賴；測試必須自行建立明確、隔離、fail-closed 相容的 runtime-manager fixture，讓乾淨環境可重現且不得降低 production fail-closed 行為。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
