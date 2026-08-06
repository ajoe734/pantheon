# Task Brief: SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Order the held closeout sink behind current controller integration
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Ownership returned from Antigravity to Claude (repeated Antigravity provider timeouts); Antigravity is now the reviewer. Evidence packet rebound to the current pair and corrected: origin/master is no longer a restore source for the PR #4590 deletions (0 of 166 present at master tip 8ec60ff74), the 7ab57c517 and a31ddbf8b CI runs were cancelled rather than pending, and evidence.json now validates against schemas/product-evidence.schema.json with zero errors. Local acceptance re-ran clean (53 passed; 31 passed 1 deselected; py_compile ok; both --validate-only profiles valid, 25 G1 creates). PR #4425 is OPEN MERGEABLE but merge-BLOCKED because .github/workflows/canonical-review-gate.yml is missing from dev and master (Human/Ops). Ready for Antigravity exact-head review at the live PR head.

## Summary
修正 current guarded dispatcher 對被 release gate 明確 hold 的 L12-CLOSE-001 誤判為 unordered overlap，同時維持所有其他 live overlap fail-closed。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
