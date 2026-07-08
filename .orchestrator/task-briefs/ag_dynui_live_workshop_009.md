# Task Brief: AG-DYNUI-LIVE-WORKSHOP-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora Strategy Workshop production tab completion
- Status: done
- Owner: Codex
- Reviewer: Codex2
- Next: Closed after task-scoped evidence commit lands and `ai-status done` is recorded.

## Summary
修正 live /agora/trading-room 的 Strategy Workshop tab：目前只裸露 workshop UUID，必須改為依 BFF workshop/cards/readiness/events/messages contract 呈現完整可操作工作坊，不可用靜態頁或 debug list 代替。

## Owner Notes

- Implemented frontend tab auto-selection so `/agora/trading-room` -> Strategy
  Workshop renders a live selected workshop runtime when the URL has no
  workshop id.
- Workshop BFF client now sends shared auth headers on list/detail/cards/
  readiness/events/message routes.
- Added focused Vitest coverage and hosted Playwright proof harness.
- Pre-deploy hosted probe against
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` still fails as
  expected before this branch is merged/deployed; see
  `docs/deployment/evidence/ag-dynui-live-tabs-009/20260708T003000Z/README.md`.
- Post-deploy hosted proof passed from `execute-plans` dev merge commit
  `9d60297e5c200d05214df7f758ee0c20c224db02` against the Pantheon dev FE/BFF.
  Evidence is in
  `docs/deployment/evidence/ag-dynui-live-tabs-009/20260708T011600Z/README.md`.
