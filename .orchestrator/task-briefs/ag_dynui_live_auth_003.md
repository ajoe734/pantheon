# Task Brief: AG-DYNUI-LIVE-AUTH-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora Trading Room frontend BFF auth headers
- Status: todo
- Owner: Claude
- Reviewer: Codex
- Next: Start execute-plans frontend auth-header fix; do not rebuild UI and do not close from local-only evidence.

## Summary
修 execute-plans Agora Trading Room frontend client: 所有 tradingRoom.ts read/write fetch 必須使用 shared BFF auth headers, 保留動態 BFF data flow, 補 Authorization 測試, PR merge 後等待 dev FE deploy 並用 live browser probe 證明 /bff/agora/trading-room 與 decision-events 都回 200。不得重做靜態 UI; 設計/合約不明時先開 blocker。
