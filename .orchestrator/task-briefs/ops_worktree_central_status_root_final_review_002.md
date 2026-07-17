# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Exact current dev 7be97fa5cffe99e704e672b5aa56abe1515da5d9 rejected. PR #3793 merged 18260e008 before owner handoff and without fresh approval. Required before re-handoff: (1) fix .orchestrator/task_archive.py fail-open parsing/provenance: malformed ls-tree rows, early cat-file EOF, OID/length/terminator/count validation, and minimal committed snapshots must fail closed; clean exact-head run currently fails test_rebuild_indexes_requires_exact_outbox_provenance_for_invalid_contracts (1 failed, 60 passed, 51 subtests). (2) remove scripts/ai_status.py read-only SH-lock TOCTOU and broad load_state exception swallowing; keep outbox check/recovery/render coherent and integrity errors visible. (3) unify exhaustive governed symlink validation; worker_runner still accepts symlinked supervisor markers and omits governed leaves. (4) restore append-only corrective closeout truth: canonical 2026-07-16T15:56:59Z dry gate probe / dirty 494 / no_upstream must be preserved with explicit correction context, not replaced by a backdated 15:37 clean/pushed snapshot. (5) fix five relevant trailing-whitespace errors, make committed brief match central lifecycle, run the complete scrubbed suite with before/after byte-identical proof and git diff --check, and hand off the pushed exact SHA before any follow-up merge. See PR #3793 review 4726406113. Keep auto-merge disabled.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
