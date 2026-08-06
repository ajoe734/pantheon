# Task Brief: SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile stale failure-streak reaper exact PR head before Wave 0 closeout
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: REOPEN at PR #4395 exact head edb1698aa6626d84039243d862dfdc33a8f87770 (== git ls-remote origin task branch == review_binding.head_sha == review-gate CheckRun 'reopen by Claude at edb1698aa662'). (1) BLOCKING: the remediation commit b10ebc75914b7ad71afb1481c83c8525d62ff1bb was never pushed - 'git branch -r --contains b10ebc759' is empty and origin/task/... is still edb1698aa. Its content does address my prior reopen (rebinds evidence.json task/review blocks to owner Antigravity / reviewer Claude / status review at head edb1698aa, sets pull_request_snapshot.state CLOSED, rewrites Required correction + recommended_canonical_action), but approval binds to the PR head, so approving now would freeze the manifest that still asserts owner=Codex2, reviewer=Antigravity, status=review_approved and PR #4385 state OPEN. Push b10ebc759 to origin so PR #4395 head advances, then hand off. (2) Re-observe before pushing: b10ebc759 is itself already stale. PR #4590 (head task/SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729) MERGED into dev at 23ae23c2185d31d2aeacafaa9b051127a6d53136 on 2026-08-06T11:57:30Z, 23 minutes before b10ebc759, and 'git diff f5e70e86e01bde005dae5fed94b151c9bc07f389 origin/dev -- docs/deployment/evidence/twelve-loop-gap/SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729/' is EMPTY, i.e. dev now carries the PR #4385 head content unrepaired: invalid anchor 9d53a94a265c55af4c8d15c50ab3751f1440ac0f is live on origin/dev (README.md x1, evidence.json x2) and the correct anchor 9d53a94a295d71ee49aea6f4b96e47fbcfd29093 appears 0 times (cat-file re-confirms 265c MISSING / 295d EXISTS). So decision.recommended_canonical_action ('must be submitted as a new PR against dev') and README Required correction must disclose the #4590 merge and target the anchor defect already ON dev, and decision.protected_merge_still_required_after_repair=true / wave0_dependency_satisfied=false must be restated given the unrepaired content already passed protected merge and the subject row is now status=blocked (owner Antigravity, reviewer Claude) awaiting Human/Ops audit-log reconciliation for the Codex2 owner drift. Keep F1/F2 findings, classification tables and the verification block unchanged.

## Summary
PR #4385 current head differs from the reviewed task row head; reconcile exact-head proof before treating stale failure-streak reaper as a Wave 0 dependency.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
