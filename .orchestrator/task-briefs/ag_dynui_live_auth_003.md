# Task Brief: AG-DYNUI-LIVE-AUTH-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora Trading Room frontend BFF auth headers
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: 7th pass (closeout): all acceptance criteria now confirmed live. The manual dev/bff workflow_dispatch deploy (28664660985) that a human approved after the 6th pass succeeded at 2026-07-03T14:02:05Z, publishing the AG-DYNUI-LIVE-AUTH-003-BFF-500-TRADING-ROOM backend fix (PR #2834 / commit 2dd82311). Live browser probe at 2026-07-03T14:01:55Z (see /tmp/agora-live-after-auth002.json, /tmp/agora-live-after-auth002.png) shows /agora/trading-room nav 200 and all BFF calls (/bff/me, /bff/agora/trading-room, /bff/agora/trading-room/decision-events, SSE stream, shell-summary) returning 200 with no console errors and no "Failed to load Trading Room" marker. This task's FE-only scope (tradingRoom.ts/headers.ts, PR #2820/75a0e857c, merged into dev) is re-confirmed correct: reran `npx vitest run src/lib/bff-v1` locally after `npm ci` — 68/68 tests pass, matching the reviewer's prior verification. Proceeding with task-closeout-finalization to move this task to `done`.

## Summary
修 execute-plans Agora Trading Room frontend client: 所有 tradingRoom.ts read/write fetch 必須使用 shared BFF auth headers, 保留動態 BFF data flow, 補 Authorization 測試, PR merge 後等待 dev FE deploy 並用 live browser probe 證明 /bff/agora/trading-room 與 decision-events 都回 200。不得重做靜態 UI; 設計/合約不明時先開 blocker。
