# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex resumed the owner lane from PR #4218 head 47aabeb8555eacc88a52fa2297375b4f0156e40d. Auto-merge remains disabled, but current origin/dev 11858f4d445565064e630cce9b89ea8b475a6598 has advanced and GitHub reports the PR conflicting. Preserve this dispatch boundary, compose current dev, reconcile the overlapping ai-status approval path without weakening exact-head review, rerun the focused matrix, refresh task evidence, push the new exact head, and dispatch Codex2 for fresh independent review. No prior approval may be reused.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
