# Task Brief: SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Order the held closeout sink behind current controller integration
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Branch re-cut linearly on origin/dev tip ab5caf7d4 so the base-branch conflict and the legacy over-length commit subject are both gone. Delivery commit 094a0f16d carries the guard, its regression suite, and the catalog inputs that the PR #4590 stale-base squash deleted. Ready for Antigravity exact-head review.

## Summary
修正 current guarded dispatcher 對被 release gate 明確 hold 的 L12-CLOSE-001 誤判為 unordered overlap，同時維持所有其他 live overlap fail-closed。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Base Branch Regression (2026-08-06)
- `origin/dev` commit `23ae23c2185d31d2aeacafaa9b051127a6d53136` ("SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729: anchor owner handoff", PR #4590, merged 2026-08-06T11:57:30Z) is a stale-base squash: 227 files changed, 1750 insertions, 47932 deletions, 166 files deleted.
- Proof that it is a regression and not an intentional removal: the resulting `scripts/dispatch_twelve_loop_gap_2026_07_26.py` blob is byte-identical to the blob at ancestor commit `780c553a0`, and 154 of the deleted files still exist on `origin/master`.
- Repaired inside this task's own envelope: the two declared script artifacts and the three `docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch` catalog inputs the dispatcher reads, each restored byte-identical to its `23ae23c21^` blob.
- Still outstanding and Human/Ops scoped: the remaining deleted files, including `.github/workflows/canonical-review-gate.yml`. While that workflow is absent from the default branch, the required "Pantheon canonical review gate" context cannot be produced from `dev`, so this PR remains unmergeable at merge time even once review passes.
