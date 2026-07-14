# Task Brief: EVOLOOP-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Research plane produces artifact v2
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed PR #3649: acceptance criteria genuinely met, re-ran e2e + service test suites independently (280 tests pass), approved. Durability/outbox gap is a known deferred scope per convergence note to LOOP-PROD-ALPHA-001.

## Summary
讓 research plane 真的產出演化產物:dispatch worker 派來的 retrain 進 research-orchestrator 成 work item,由 training-session/optimizer 執行一次真實的最小 retrain(參數變異),產出 artifact v2 註冊進 registry,lineage 帶 {v1, decision_id, work_item_id, session_id}。v2 的參數必須與 v1 有真實差異,不得假輸出。
