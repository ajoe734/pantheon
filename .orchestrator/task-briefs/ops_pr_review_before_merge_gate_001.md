# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex anchored the resumed dispatch at de306595d8a84ebce323e0aaaa0efdf8ed5f16b5, composed current origin/dev 11858f4d445565064e630cce9b89ea8b475a6598 at b05189382fe70426b23a7180b51c891f8dce95b0, preserved the newer GitHub review bridge through the ai-status conflicts, and passed 360 focused tests plus 31 subtests. Commit the refreshed evidence, run static gates, push through task_finalize with auto-merge still disabled, verify PR #4218 names the new exact head and current dev remains contained, then hand off to Codex2 for a fresh independent exact-head review. No prior approval may be reused.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
