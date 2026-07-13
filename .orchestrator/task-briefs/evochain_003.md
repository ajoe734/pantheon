# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-checked 2026-07-13: dev HEAD e13228e74, no new EVOCHAIN-003 PRs beyond merged #3533/#3541/#3549/#3552 (already reviewed on merits). Committed pending task-brief sync (2d0918c83) and pushed. Approve still classifier-blocked as self-approval (reviewer=Claude); needs human to run approve. No further action possible from this side.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
