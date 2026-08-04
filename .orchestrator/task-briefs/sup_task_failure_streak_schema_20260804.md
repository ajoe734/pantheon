# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review rejected: the committed REVIEW_FILE is present at PR #4533 head b5509721342745174d25ce10134be342b80c50c1 (verified through GitHub tree, Contents API, and compare), but scripts/ai_status.py review_evidence_file_committed calls gh api with -f ref=<sha>, which makes gh issue POST instead of the required Contents GET and falsely reports the manifest absent. Change this lookup to an explicit GET/query ref, add a focused regression that proves an exact-head manifest validates, update the evidence manifest, then request exact-head re-review.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
