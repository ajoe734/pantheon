# Task Brief: DEVLOOP-CENSUS

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Right-half census/probe script (repeatable progress meter)
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved: implementation correctly distinguishes hard failures from empty ledgers, telemetry fallback guard prevents false positive signals, all acceptance criteria met. Returned to Codex for closeout.

## Summary
做一個可重複的右半 census 腳本(新檔 scripts/devloop_census.py):用 dev BFF stub auth(Bearer op-dev:admin:mfa)點 telemetry/loop-runs/approvals/evolution/incidents/rollbacks 計數,輸出右半是否開始有資料。供後續每輪量測進度。
