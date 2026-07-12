# Task Brief: AG-GAP-011

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile nested FE checkouts; enforce canonical execute-plans
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Changes required: /home/lupin/code/pantheon/execute-plans still exists with src/management and tests/e2e, so the documented no-nested-checkouts rule and all-stale-checkouts-purged claim remain false. Audit/salvage those files without folding the unrelated dirty live Pantheon checkout into this task, remove the nested artifact safely, update the evidence with exact patch-equivalence proof for 821ad41, and request re-review.

## Summary
盤點 .fe-ep 與 .fe-human-inbox-persona-focus 未推送的工作、salvage 後清除 stale checkout；成文規則：FE 只經 ajoe734/execute-plans@dev。
