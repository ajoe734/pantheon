# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Exact head 347fba342ffb6c3f1d5abc8c869d0d32cef1843f rejected again; no new candidate followed the prior exact-SHA rejection. Compose origin/dev@88cf3c65b5428bc649ed7b76a117b274776a96a9 (candidate is behind 36), then fix strict archive/outbox validation and FIFO/TOCTOU-safe archive reads; bind status leases to normalized logical actor and require leases for auto workers; enforce heartbeat/status role plus run-id and the complete governed-path matrix; restore signal handlers; make scrubbed suites leave tracked mirrors byte-identical; fix five git diff-check errors. Keep auto-merge disabled, rerun complete exact-head suites, push a new SHA, and re-handoff.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
