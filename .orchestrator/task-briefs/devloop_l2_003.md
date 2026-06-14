# Task Brief: DEVLOOP-L2-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix deployment-plan.current_stage consistency
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by Codex while Claude is dispatch-paused.

## Summary
修 deployment-plan.current_stage 永遠停在 'none' 但 binding 已 active/paper 的一致性 bug;binding 啟用時 plan lifecycle 欄位應前進到對應 stage。
