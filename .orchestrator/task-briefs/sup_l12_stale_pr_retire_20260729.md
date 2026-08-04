# Task Brief: SUP-L12-STALE-PR-RETIRE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Retire stale L12 PRs after 1025Z gap audit
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Evidence refreshed from exact GitHub heads and governed task rows. #4367 is already closed/unmerged and remains retired; #4364, #4297, and #4313 remain open because they are active product-proof, review-refresh, or blocked-closeout paths. Await Codex2 independent review of the task evidence manifest.

## Summary
Retire or supersede stale L12 PRs without closing active product proof.

## 2026-08-04 Audit Result

- `#4367` is the retired duplicate of merged `#4365` delivery and `#4366`
  closeout evidence; it was already closed without merge.
- `#4364` is active `L12-VERIFY-OBS-001` product proof and must remain open
  pending an exact current-head refresh/review.
- `#4297` remains open because its `review_approved` row has a stale review
  binding and needs an exact-head refresh; `#4313` remains open because its
  canonical closeout row is blocked waiting for Codex2.
- See `docs/deployment/evidence/twelve-loop-gap/SUP-L12-STALE-PR-RETIRE-20260729/`
  for the PR/head/task/action table and verification record.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
