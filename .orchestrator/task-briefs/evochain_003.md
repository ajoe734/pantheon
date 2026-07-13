# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Independent review complete: all 4 PRs (#3533 postmortem publisher gap closure, #3541 delivery+caller tests, #3549 replay reset+compose URL wiring, #3552 durable outbox+hardened dedup) already merged into dev; task branch HEAD is an ancestor of origin/dev (nothing outstanding to merge). Verified against all 4 acceptance criteria: (1) incident resolve/close enqueues to UnifiedOutboxStore -> POST /api/postmortems/consume-resolved-incident creates draft postmortem; (2) publish transition enqueues outbox -> POST /api/evolution/proposals/from-postmortem-published -> postmortem_bridge.build_published_postmortem_proposal_request -> proposal admitted; (3) postmortem_bridge.py confirmed pure (no I/O, no store writes); (4) dedupe enforced at both hops (draft consumer checks existing postmortem-for-incident; proposal admission matches bridge_key+target_type+target_id+incident_cluster, returns 200 on dup, 409 on unrelated conflict). Full suite services/evolution+incident+incidents+postmortems: 324 passed (one order-dependent flake on first run, clean on rerun). Attempted approve -> denied by self-approval classifier (reviewer reassigned to Claude but still same agent-family pattern). Needs human to run the approve step.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
