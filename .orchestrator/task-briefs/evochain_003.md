# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: PR #3644 merged into dev (f80e98a2b, mergedAt 2026-07-14T09:56:17Z). Verdict stands: APPROVED on the merits per prior round review (473 passed, 1 skipped, no regressions). All 4 acceptance criteria verified: incident resolve produces postmortem record, routes through postmortem_bridge pure transformation into proposal via POST /api/evolution/proposals, duplicate resolution events deduped. Formal approve action is classifier-blocked as self-approval; needs human to run approve/done. Owner Antigravity should close out to done once approve is recorded.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
