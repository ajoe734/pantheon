# Task Brief: EVOLOOP-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote pipeline: registry to LEAN binding
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Review still stands (verified pre-reassignment): PR #3629 (implementation) and PR #3633 (live dev evidence) both merged; all 4 acceptance criteria confirmed met (service-APIs-only promote rb-abb82fd -> rb-9d952e, runtime_id matched at every stage, exact rollback+re-promote demonstrated, no hand-edits). approve is classifier-blocked as self-approval since the same automated-worker system authored the code and drove the review -- needs a human to run 'ai_status.py approve EVOLOOP-006'. Owner was auto-reassigned Codex2 -> Antigravity; this does not change the review outcome or the pending human-approve blocker.

## Summary
跑通 promote 管線:registry artifact → deployment plan → 以管線(非手動改 store)替換一個 rescue 佔位 binding 成 pipeline-managed binding。遵守 RuntimeBinding 契約(runtime_id 必須等於容器 PANTHEON_RUNTIME_ID;參照 paper-binding-rescue runbook)。rollback 路徑要文件化並實測(re-bind 前一個 artifact)。
