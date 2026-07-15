# Task Brief: LOOP-PROD-RUNTIME-BOOT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Shared runtime/task/audit lock protocol bootstrap
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Codex2 review requested changes on PR #3652: status duplicate issue no longer blocks basic ai-status read, but acceptance is not met. Required: fix or formally redesign canonical_writer_guard so PANTHEON_ALLOW_ISOLATED_LEGACY_WRITES cannot authorize writes to configured PANTHEON_STATUS_ROOT and add the matching regression; provide/align the declared capability and signed completion path (.orchestrator/runtime-task-audit-lock-capability.json and completion/capability evidence) or amend the task contract before review approval; refresh README/evidence/checks to match active owner Antigravity, current head/inventory, and resolved duplicate-status-root investigation.

## Summary
在 48 個 primary task materialization 前，讓 runtime admission、canonical task state 與 activity audit 的所有 writer 共用穩定 inode lock，並以 process/crash/recovery evidence 證明可安全 dry-run/apply。
