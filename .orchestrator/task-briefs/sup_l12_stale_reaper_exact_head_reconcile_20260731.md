# Task Brief: SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile stale failure-streak reaper exact PR head before Wave 0 closeout
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewer Claude reviewed PR #4395 exact head edb1698aa6626d84039243d862dfdc33a8f87770 (== origin task head == review_binding.head_sha). CONFIRMED CORRECT and not to be redone: F1 anchor proof (9d53a94a265c55af4c8d15c50ab3751f1440ac0f MISSING, 9d53a94a295d71ee49aea6f4b96e47fbcfd29093 EXISTS); range-diff patch-equivalence b41a69c1=9d53a94a2, 86dd9006=833c3658, f5e70e86 sole added commit; path counts 18/4/5, merge-base 575040212, reviewed head NOT ancestor of f5e70e86; .orchestrator/config.json absent from 6f87a207...f5e70e86 and from this PR diff (3 in-scope files). REOPENED for two stale bindings that approval would freeze. (1) evidence.json task block still binds owner=Codex2, reviewer=Antigravity, status=review_approved, and its review block binds reviewed_task_head_sha=607a474688566b1a62c4ec24998c4d6864d62a88, but the live row is owner=Antigravity, reviewer=Claude, status=review at head edb1698aa, and commits 9b920afc/f68827c8/edb1698a modified README.md and evidence.json after that cited reviewed head - so the packet asserts review_approved for content its own cited review never covered. Rebind the task and review blocks to the current cycle (owner Antigravity, reviewer Claude, PR #4395 head edb1698aa, status review) and keep the earlier Antigravity decision as an explicit history entry. (2) The external snapshot is stale in a way that changes the recommendation: PR #4385 is now CLOSED (manifest records state OPEN, observed_at 2026-07-31T12:17:11Z), SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729 is now review_approved with owner Antigravity/reviewer Claude, and the invalid anchor 9d53a94a265c is still live on origin/dev (README.md x1, evidence.json x2), so F1 was never corrected. Re-observe and restate decision.recommended_canonical_action against live state - the narrow anchor correction now needs a new PR against dev or an explicit supersede, not a push to closed PR #4385 - and refresh pull_request_snapshot plus the README Required correction section. Keep F1/F2 findings, the classification tables, and the verification block unchanged. Informational: PR #4395 is mergeStateStatus BLOCKED although all four required contexts read success at edb1698aa.

## Summary
PR #4385 current head differs from the reviewed task row head; reconcile exact-head proof before treating stale failure-streak reaper as a Wave 0 dependency.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
