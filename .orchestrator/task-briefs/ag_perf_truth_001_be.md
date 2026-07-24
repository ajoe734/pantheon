# Task Brief: AG-PERF-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governed Agora performance projection and actions
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: scoping, live gate, canonical lineage, typed unavailable, durable receipts, idempotency/CAS, viewer refusal, and no-order proofs all verified; reviewer independently re-ran 13 focused tests (all green) and smoked the three main.app routes (401 fail-closed). Returned to Codex for finalization.

## Summary
新增真實績效/介入/執行歷史/調整建議 projection 與 governed apply/reject/return receipt；缺資料回 unavailable，不得造數或造結論。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
