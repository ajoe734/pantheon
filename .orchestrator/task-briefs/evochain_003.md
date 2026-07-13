# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Rewoken (review_ready_dispatch); re-checked gh pr list, no new EVOCHAIN-003 PRs beyond merged #3533/#3541/#3549/#3552 (already reviewed on merits, 324 tests pass). Still genuine wait state for human approve (self-approval classifier-blocked). No action taken this cycle.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
