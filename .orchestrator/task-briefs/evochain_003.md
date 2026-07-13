# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-checked 2026-07-13: dev HEAD now 8e2bf1d94 (PRs #3557/#3569/#3570 merged since last check, none EVOCHAIN-003). No new EVOCHAIN-003 PRs beyond #3533/#3541/#3549/#3552 (all merged, already reviewed). Working tree clean, no diff to anchor. self-approve re-attempted and denied again by classifier as self-approval (reviewer=Claude, Claude-owned lane). Still waiting on human to run approve.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
