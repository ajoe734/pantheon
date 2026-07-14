# Task Brief: LOOP-PROD-RUNTIME-BOOT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Shared runtime/task/audit lock protocol bootstrap
- Status: todo
- Owner: Codex2
- Reviewer: Codex
- Next: Admit a fleet worker to install and prove the shared runtime/task/audit lock protocol.

## Summary
在 48 個 primary task materialization 前，讓 runtime admission、canonical task state 與 activity audit 的所有 writer 共用穩定 inode lock，並以 process/crash/recovery evidence 證明可安全 dry-run/apply。
