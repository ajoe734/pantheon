# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-checked 2026-07-13: dev HEAD advanced to 4fa042ddd (PR #3566 LOOP-PROD-001 fix merged since), no new EVOCHAIN-003 PRs beyond #3533/#3541/#3549/#3552 (all merged, no new commits on task branch). Status remains 'review'; self-approve still denied by classifier as self-approval (reviewer=Claude, Claude-owned lane). No code/doc changes needed this cycle. Still waiting on human to run approve.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
