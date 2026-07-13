# Task Brief: EVOCHAIN-008

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FE data-source badge semantics (live-degraded vs snapshot)
- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity
- Next: PR #298 (execute-plans) had drifted BEHIND origin/dev again; re-synced task/EVOCHAIN-008 onto origin/dev in the execute-plans worktree and re-ran the focused TopBar suite (10/10 passing) before pushing `c23e165`. Also re-synced the pantheon-side task/EVOCHAIN-008 branch (PR #3522) onto origin/dev after it independently drifted BEHIND. Both PRs still require a human to merge (self-merge governance block); hosted `audit:render` evidence for acceptance item 4 remains outstanding until PR #298 merges and dev redeploys. Returning to review_approved steady state pending human merge.

## Summary
修正 execute-plans 管理台的資料來源徽章語意：degraded 且 source 為 live 組合時顯示「LIVE（部分降級）」並可看到是哪些 surface 降級；「SNAPSHOT DATA」只保留給真的由快照供資料的情況。
