# Task Brief: SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile stale failure-streak reaper exact PR head before Wave 0 closeout
- Status: review_approved
- Owner: Codex2
- Reviewer: Antigravity
- Next: Independently verified PR #4385 exact head reconciliation evidence in docs/deployment/evidence/twelve-loop-gap/SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731/evidence.json. Confirmed F1 blocking finding: PR #4385 head f5e70e86e01bde005dae5fed94b151c9bc07f389 references nonexistent implementation anchor 9d53a94a265c55af4c8d15c50ab3751f1440ac0f instead of actual commit 9d53a94a295d71ee49aea6f4b96e47fbcfd29093. PR #4385 exact head f5e70e86 is rejected for exact-head approval and requires subject task SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729 to be reopened for anchor SHA correction.

## Summary
PR #4385 current head differs from the reviewed task row head; reconcile exact-head proof before treating stale failure-streak reaper as a Wave 0 dependency.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
