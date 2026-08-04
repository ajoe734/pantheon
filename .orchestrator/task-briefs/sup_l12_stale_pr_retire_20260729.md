# Task Brief: SUP-L12-STALE-PR-RETIRE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Retire stale L12 PRs after 1025Z gap audit
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Rework required before approval of PR #4372 head 95b3ddaf4b38f8b3eddbf29aa94b4c3b90968b29: (1) committed README.md has a trailing blank line; git diff --check origin/dev...HEAD reports docs/deployment/evidence/twelve-loop-gap/SUP-L12-STALE-PR-RETIRE-20260729/README.md:21 new blank line at EOF. Remove it and regenerate evidence.sha256. (2) the pushed task brief falsely records status review_approved and claims an independent review at old head eedccf8c6aaf495d85e6ff2b07fa4a678b000db4, while evidence.json correctly says pending_independent_review and canonical task state is review at head 95b3ddaf4b38f8b3eddbf29aa94b4c3b90968b29. Commit and push the brief correction (do not amend), then request a fresh exact-head review.

## Summary
Retire or supersede stale L12 PRs without closing active product proof.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
