# Task Brief: EVOCHAIN-008

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FE data-source badge semantics (live-degraded vs snapshot)
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: FE PR #298 merged and deployed at `89515d82`; 78/78 hosted routes passed and the live-degraded badge screenshot was archived. Finalize Pantheon evidence PR #3522, then close the task.

## Summary
修正 execute-plans 管理台的資料來源徽章語意：degraded 且 source 為 live 組合時顯示「LIVE（部分降級）」並可看到是哪些 surface 降級；「SNAPSHOT DATA」只保留給真的由快照供資料的情況。
