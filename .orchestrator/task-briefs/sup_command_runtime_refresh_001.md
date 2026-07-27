# Task Brief: SUP-COMMAND-RUNTIME-REFRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Refresh installed supervisor command runtime safely
- Status: review
- Owner: Codex
- Reviewer: Codex2
- Next: Final runtime proof is complete in PR #4257 with auto-merge disabled: candidate 29054ab270d552a56ed071cedf3f45150e948b6a is live after an executed rollback/roll-forward drill, config sha256 stayed byte-identical, leases and queue owners remained coherent without duplicate dispatch, and authoritative projection checks passed. Codex2 must independently review the exact final PR head, bind REVIEW_PR=4257 plus the full REVIEW_HEAD_SHA and evidence.json REVIEW_FILE, and approve only that head before merge and owner done.

## Summary
在 supervisor truth 修復合併後，將 governed command runtime 更新到精確 accepted dev；重用既有 config，不改 config，不中斷 active lease，保留 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
