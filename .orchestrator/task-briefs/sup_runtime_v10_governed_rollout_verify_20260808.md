# Task Brief: SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Run governed V10 supervisor runtime promotion and verify recovery
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Helper-claimed by Codex while Codex2 is dispatch-paused previous owner Codex2 becomes reviewer.

## Summary
Human/Ops 已授權後續由 supervisor 派工。本任務只可透過既有 transactional promotion 執行：從已合併 dev 產生 immutable candidate，驗證 PYTHONDONTWRITEBYTECODE=1、-B 與 status child 不產生 pycache。若 incumbent identity、環境或 config migration 無法綁定，必須 fail closed、保存證據並建立 source-only follow-up；不得手改 live config、直接終止 process 或手動 checkout。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
