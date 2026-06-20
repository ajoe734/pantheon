# Task Brief: AG-XR-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Cross-repo generated types and drift CI
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: restored after ai-status revert incident

## Summary
依 SD §24/§23.5 從 AG-XR-001 的 OpenAPI/schema bundle 在 execute-plans 生成 src/lib/bff-v1/agora/types.ts,並加一個 contract-drift CI check:當 pantheon OpenAPI/schema sha256 與 execute-plans 生成快照不一致時 CI 紅。確保跨 repo 以 contract version 對齊。
