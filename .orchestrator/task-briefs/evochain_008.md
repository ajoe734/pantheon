# Task Brief: EVOCHAIN-008

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FE data-source badge semantics (live-degraded vs snapshot)
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Auto-reassigned ownership from Claude to Codex after repeated Claude terminal: Worker exited before the task reached a terminal status.

## Summary
修正 execute-plans 管理台的資料來源徽章語意：degraded 且 source 為 live 組合時顯示「LIVE（部分降級）」並可看到是哪些 surface 降級；「SNAPSHOT DATA」只保留給真的由快照供資料的情況。
