# Task Brief: AG-DYNUI-PROD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Restore Agora DYNUI source and task truth
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Restore source/deploy/task truth so downstream Agora DYNUI production-gap workers can continue from committed evidence instead of missing archive state.

## Summary
修復 Agora DYNUI 設計來源與任務真相：確認 AI Trading Desk Design.zip 或替代 closure pack 的 canonical 位置，恢復缺失 archive/task truth，列出舊 DYNUI PR 完成與未完成的邊界，讓後續 fleet 能接續。

## Task-Owned Artifact

- Source/task truth map:
  `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md`
- Reconstructed archive snapshots:
  `ai-task-archive/tasks/AG-BE-DYNUI-001.json`,
  `AG-BE-DYNUI-002.json`, `AG-BE-DYNUI-003.json`,
  `AG-XR-DYNUI-001.json`, `AG-FE-DYNUI-001.json`,
  `AG-FE-DYNUI-002.json`, `AG-FE-DYNUI-003.json`,
  `AG-FE-DYNUI-004.json`, and `AG-FE-DYNUI-005.json`.

## Non-Goals

- Do not implement Agora frontend or BFF runtime behavior in this task.
- Do not use `/home/lupin/code/pantheon/.fe-ep` as deploy/source truth.
- Do not mark `AG-E2E-DYNUI-001` complete; it remains replaced by
  `AG-DYNUI-PROD-006`.
