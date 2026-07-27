# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner repaired the rejected exact-head path: gated integration now proves current origin/dev is an ancestor of the reviewer-bound oid, smokes that immutable oid in a detached worktree, never rebases or pushes it, and requests owner refresh/reapproval only when the base is genuinely absent. A real-git merge-rich regression reaches gh pr merge --match-head-commit with 0 base-only commits and no rebase/push. Current origin/dev a6966b13d84430387da9c3a33fcf224c841bc5c6 is composed at 8aaf154ca07e4d46f706a50a259e2c0d6fd553c1; 92 gate, 9 integrator, 58 helper, 2 refspec, 24 triage, 17 index, and 144 ai-status tests passed, with 346 passed plus 31 subtests in the combined matrix. Push the final evidence head through task_finalize, confirm PR #4218 is exact-current with autoMergeRequest null, then dispatch Codex2 for a fresh exact-head review; no prior approval may be reused.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
