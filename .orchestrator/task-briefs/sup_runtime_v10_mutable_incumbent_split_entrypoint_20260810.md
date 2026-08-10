# Task Brief: SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind split mutable incumbent entrypoint provenance
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Review rejected: PR #4722 head is 9d3da5caca0598f7cf7748bfcb5dec67211e73eb, but the required split command-root repair is only local at 513212ab7343063deb9084e4805bf91601b9cd6b. Push the corrected task head (or a successor) to this branch/PR, then wait for checks. Before re-review, correct the committed task evidence manifest to record the exact full-suite result: independent collection is 328 tests (split run: 7 passed, 321 deselected), not the current 130 claim; run the manifest command and record its exit/result. Ensure this corrected manifest is present in the PR diff and request a fresh exact-head review.

## Summary
The 0305c861 candidate is clean, but the live legacy supervisor has mutable dev-root as cwd while its exact configured argv entrypoint comes from a different command runtime. Implement only descriptor-bound split-root provenance capture plus a clean rollback boundary inside the existing transaction; do not revive sync-script signalling or weaken new-runtime identity.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
