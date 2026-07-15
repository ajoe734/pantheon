# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Review changes required: Postgres put is insert-only and breaks incident/outbox updates; critical+frozen publishes invalid freeze/frozen and dead-letters; terminal incidents can regress without CAS and strand the event; direct resolved-to-closed can replace first resolved_at. Keep postmortem_bridge pure. See support/reviews/EVOCHAIN-003-review-codex.md; focused review suite 77 passed but misses these valid paths.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
