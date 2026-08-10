# Task Brief: SUP-RUNTIME-V10-ROLLBACK-COLLISION-SAFE-DESTINATION-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make rollback materialization collision-safe without root reuse
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Independent review rejected exact head 863aaa9b1424aa1f2d058959ddc713c72df7d6d1. PR #4728 Commit trailers fails because 863aaa9b subject is 75 chars and 9626cba21 subject is 85 chars (limit 72); replace or squash the PR history so every origin/dev..HEAD commit has a <=72-char subject and required trailers. materialize_immutable_rollback_runtime still has a check-then-os.rename destination race: an empty or symlinked rollback destination created after exists()/is_symlink() can be replaced; atomically reserve or no-replace-install the destination, fail closed without overwrite or side effects, and cover an injected post-check collision. The new path/device/inode test uses fabricated Mock identity values, not real materialized identities; add an end-to-end occupied-direct-destination test that verifies real candidate and rollback path+device+inode separation plus rollback launch/config binding. Re-run focused and full promotion suites plus git diff --check, and update the committed evidence manifest for the new exact head.

## Summary
Repair the next narrow rollback-materialization collision discovered after PR #4726: keep the existing different-root 0305 runtime untouched and provide a deterministic, independently verified fresh rollback identity for the governed promotion transaction.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
