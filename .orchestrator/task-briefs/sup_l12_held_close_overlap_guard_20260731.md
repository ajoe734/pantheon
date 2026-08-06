# Task Brief: SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Order the held closeout sink behind current controller integration
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Evidence head_sha advanced to PR head 7ab57c517 (confirmed on GitHub); CI runs 31120873389/31120876932 queued at 16:44Z. Evidence sequence 5 recorded. PR #4425 is OPEN MERGEABLE. Required canonical-review-gate.yml still missing from dev (Human/Ops blocker). Ready for Claude exact-head review at 7ab57c517 once CI resolves.

## Summary
修正 current guarded dispatcher 對被 release gate 明確 hold 的 L12-CLOSE-001 誤判為 unordered overlap，同時維持所有其他 live overlap fail-closed。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
