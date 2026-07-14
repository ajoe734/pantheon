# Task Brief: EVOLOOP-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Research plane produces artifact v2
- Status: todo
- Owner: Antigravity
- Reviewer: Claude
- Next: Auto-reassigned EVOLOOP-004 away from unavailable lane Codex (disabled, paused, sidecar-only, or auth-down); owner Codex -> Antigravity.

## Summary
讓 research plane 真的產出演化產物:dispatch worker 派來的 retrain 進 research-orchestrator 成 work item,由 training-session/optimizer 執行一次真實的最小 retrain(參數變異),產出 artifact v2 註冊進 registry,lineage 帶 {v1, decision_id, work_item_id, session_id}。v2 的參數必須與 v1 有真實差異,不得假輸出。
