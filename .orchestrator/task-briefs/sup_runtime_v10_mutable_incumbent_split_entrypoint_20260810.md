# Task Brief: SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind split mutable incumbent entrypoint provenance
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Reject: _mutable_process_contract still sets ExpectedSupervisorProcessContract.command_root=str(cwd.path) (line 6609). For the required live split-root process, PANTHEON_COMMAND_ROOT is the argv-derived command-runtime entrypoint root, so discovery still fails PANTHEON_COMMAND_ROOT mismatch after the old argv/cwd check is removed. The new positive test leaves reader PANTHEON_COMMAND_ROOT at the cwd, so it misses this. Bind the legacy split expected contract to the descriptor-validated entrypoint-root identity in capture, prepare baseline, and transaction revalidation; add a live-like positive test (cwd dev-root, argv/env command root runtime) and an adversarial wrong-command-root test. Keep new candidate/rollback single-root contracts strict.

## Summary
The 0305c861 candidate is clean, but the live legacy supervisor has mutable dev-root as cwd while its exact configured argv entrypoint comes from a different command runtime. Implement only descriptor-bound split-root provenance capture plus a clean rollback boundary inside the existing transaction; do not revive sync-script signalling or weaken new-runtime identity.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
