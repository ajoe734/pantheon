# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude
- Next: Codex2 recovered commits 4e5562d42 and 837bb253d after operator re-dispatch; independently auditing the five post-PR-#3682 P1 remediations and rerunning acceptance before a follow-up PR and Claude re-review.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
