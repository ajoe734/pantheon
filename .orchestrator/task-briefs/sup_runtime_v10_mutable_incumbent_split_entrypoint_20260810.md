# Task Brief: SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind split mutable incumbent entrypoint provenance
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Independent code review and verification passed (328/328 promotion tests; split 7 passed, 321 deselected; git diff --check clean), but PR #4722 head 2798c8aee468286ea772d9b4d8be702934b7279c is unmergeable: required Commit trailers CI fails because 513212ab7343063deb9084e4805bf91601b9cd6b has a 113-character subject and 2798c8aee468286ea772d9b4d8be702934b7279c has a 100-character subject. Rebuild the reviewed source/evidence commits from dev on a clean successor branch/PR with every subject <=72 characters and required trailers; do not force-push/amend the already-pushed branch. Ensure the manifest remains committed, then request fresh exact-head review.

## Summary
The 0305c861 candidate is clean, but the live legacy supervisor has mutable dev-root as cwd while its exact configured argv entrypoint comes from a different command runtime. Implement only descriptor-bound split-root provenance capture plus a clean rollback boundary inside the existing transaction; do not revive sync-script signalling or weaken new-runtime identity.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
