# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review approved PR #4218 exact head d489750abe396e8696bba2c502d23d8f8640ffd9: origin/dev 11858f4d445565064e630cce9b89ea8b475a6598 is contained (0 base-only), autoMergeRequest is null, CI checks pass, independent 360-test matrix plus 31 subtests and all static gates pass, and AC1-AC6 exact-head/rejection/ambiguity/revocation/compatibility/archive behavior is verified.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
