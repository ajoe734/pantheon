# Task Brief: SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Owner reassigned Codex -> Codex2 because both Codex execution slots are occupied while one Codex2 slot became available after exact-head review. This is concurrency only, not a quota condition. Execute the already-authorized governed rollout from merged dev f7933594 using only sync-dev-root.sh and promote-supervisor-runtime.sh; verify immutable candidate identity, three fresh loops, authoritative-shadow catchup, queue-worker parity, provider baseline and rollback evidence, then hand off to Antigravity.
- Status: in_progress
- Owner: Codex2
- Reviewer: Antigravity
- Next: Ownership updated

## Summary
Human/Ops 已授權後續由 supervisor 派工。本任務只可透過既有 transactional promotion 執行：從已合併 dev 產生 immutable candidate，驗證 PYTHONDONTWRITEBYTECODE=1、-B 與 status child 不產生 pycache。若 incumbent identity、環境或 config migration 無法綁定，必須 fail closed、保存證據並建立 source-only follow-up；不得手改 live config、直接終止 process 或手動 checkout。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
