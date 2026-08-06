# Task Brief: SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Move #4396 running-owner exact-head proof through governed PR/closeout
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Re-verified every recorded blocker against live state; none still apply. PR #4396 is MERGED into dev (squash 9cb030dc1b6944334f3717af6c0d5f2fc5f10cd9, 2026-08-05T02:00:30Z) and its reconcile task is archived done, so the draft/blocked integration gap is closed. The 2026-08-04 blockers are resolved on dev: ai_status.py now issues an explicit `--method GET` contents query with its own regression test, and dev branch protection again requires "Pantheon canonical review gate". This branch's duplicate ai_status.py fix was dropped in favour of dev's, and the over-length commit that failed the trailer gate was removed by resetting onto the dev tip. Subject PR #4386 stays explicitly not-complete (open, CONFLICTING, row SUP-L12-RUNNING-OWNER-RECONCILE-20260729 is todo). Awaiting exact-head review of PR #4550 from Antigravity.

## Summary
Resolve the draft ReviewBus PR #4396 integration gap so current-head running-owner support evidence is either governably integrated or explicitly routed without draft-PR blockage.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
