# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex resumed the owner lane, composed current origin/dev b81edf76dfc14087dd7d5e3a6599448cb9d0bb09 at 8d142e58a983ee9ea0def27091a295a1937ea461, and revalidated the repaired immutable exact-head path: 92 gate, 9 integrator, 58 helper, 2 refspec, 24 triage, 17 index, and 144 ai-status tests passed, with 346 passed plus 31 subtests in the combined matrix; static checks are clean. Commit and push the refreshed task evidence through task_finalize, confirm PR #4218 names that exact head with autoMergeRequest null and current dev contained, then dispatch Codex2 for a fresh independent exact-head review. No prior approval may be reused.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
