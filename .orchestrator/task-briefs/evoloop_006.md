# Task Brief: EVOLOOP-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote pipeline: registry to LEAN binding
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Run the promote pipeline: registry -> deployment plan -> replace one rescue binding; document and test rollback.

## Summary
跑通 promote 管線:registry artifact → deployment plan → 以管線(非手動改 store)替換一個 rescue 佔位 binding 成 pipeline-managed binding。遵守 RuntimeBinding 契約(runtime_id 必須等於容器 PANTHEON_RUNTIME_ID;參照 paper-binding-rescue runbook)。rollback 路徑要文件化並實測(re-bind 前一個 artifact)。
