# Task Brief: SUP-L12-STALE-PR-RETIRE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Retire stale L12 PRs after 1025Z gap audit
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude2
- Next: Human/Ops pre-review gate: PR #4372 is OPEN but BEHIND current dev at head c98673fe0dbe564f77117ceb09fd3214c34532d7. Its evidence also cites stale PR #4364 head 1131ea3d while PR #4364 is now ecadf3ad and itself behind. Rebase onto current origin/dev, refresh evidence against the live PR #4364 head/status, rerun required CI, then handoff to Claude2 for exact-head review.

## Summary
Retire or supersede stale L12 PRs without closing active product proof.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
