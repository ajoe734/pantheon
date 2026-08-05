# Task Brief: OPS-CLOSEOUT-OWNER-REASSIGN-NO-HUMAN-SIGNOFF-20260805

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Auto-verify owner reassignment chain so closeout needs no human sign-off (P0)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Fixed owner reassignment chain verification, evidence reviewer independence check, and PR #4573 opened

## Summary
目標：任務收尾（done）不該再需要 Human/Ops 手動簽名。目前 validate_merged_done_evidence／command_done 對 owner 身分不連續是硬性擋死（逐字比對證據檔案 Owner 欄位跟目前 owner，對不上就 fail closed，沒有備援），但對 reviewer 身分不連續已經有 _verified_reviewer_reassignment 這條備援路徑：去查稽核紀錄裡的 task_reassigned 事件鏈，驗證每一步轉派都合法、身分連續，驗證通過就自動放行，不需要人工。owner 轉派（多半是因為 provider 額度用完/暫時不可用，是正常、會反覆發生的事件，不是異常)完全沒有對應的自動驗證路徑，所以每次 owner 被轉派過的任務要收尾，現在都得靠人（今天 SUP-REVIEW-HANDOFF-OWNER-STABILITY-20260731 就是這樣手動收掉的，那不該是常態）。要修的是：比照 reviewer 那條路徑，做一條等價的 owner 版本——沿著 task_reassigned 事件鏈驗證整個 owner 轉派歷史合法、連續，驗證通過就讓 done／reconcile 這類收尾動作自動放行，徹底不需要人工簽名。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
