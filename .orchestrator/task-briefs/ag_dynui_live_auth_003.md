# Task Brief: AG-DYNUI-LIVE-AUTH-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora Trading Room frontend BFF auth headers
- Status: live verified; owner closeout pending task archival
- Owner: Claude
- Reviewer: Codex
- Next: Closeout evidence is complete. execute-plans PR #168 merged
  `ffbc2357f23b1a728ed6794d2231356ff28f16ed`; dev FE deploy
  `28664312966` and FE-BFF integration gate `28664312972` succeeded.
  Pantheon BFF follow-up PR #2834 merged
  `2dd82311dcd95b9ebe4ed33a8d16666ecbb82791`; Nonprod Deploy
  `28664660985` succeeded. Live browser probe at `2026-07-03T14:01:55Z`
  showed `/agora/trading-room` nav 200, Trading Room aggregate and
  decision-events 200, no console errors, no `Failed to load Trading Room`,
  and screenshot `/tmp/agora-live-after-auth002.png` shows the dark Agora
  layout. Closeout evidence PR (#2836) is merged into dev; owner is now
  running task-closeout-finalization and moving this task to `done`.

## Summary
修 execute-plans Agora Trading Room frontend client: 所有 tradingRoom.ts read/write fetch 必須使用 shared BFF auth headers, 保留動態 BFF data flow, 補 Authorization 測試, PR merge 後等待 dev FE deploy 並用 live browser probe 證明 /bff/agora/trading-room 與 decision-events 都回 200。不得重做靜態 UI; 設計/合約不明時先開 blocker。
