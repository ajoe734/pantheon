# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-checked 2026-07-13T20+: dev HEAD now at e7ad6a062 (PR #3565 LOOP-PROD-001 merged), still no new EVOCHAIN-003 PRs beyond #3533/#3541/#3549/#3552 (all already merged into dev, task branch content is ancestor of dev). Status remains review; self-approval still classifier-blocked for Claude as reviewer. Waiting on human to run approve.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
