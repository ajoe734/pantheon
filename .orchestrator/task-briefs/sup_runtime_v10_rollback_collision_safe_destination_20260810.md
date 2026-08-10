# Task Brief: SUP-RUNTIME-V10-ROLLBACK-COLLISION-SAFE-DESTINATION-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make rollback materialization collision-safe without root reuse
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Review rejected for exact head 9626cba21afa733def9eb06221af228a8491f986. Acceptance requires a fresh rollback identity and fail-closed handling of every pre-existing destination. However scripts/promote_supervisor_runtime.py:6919-6952 verifies then returns an existing rollback-command-runtimes/<sha> identity, so a prior rollback destination is reused; tests replaced the former rejection regressions with success cases and do not assert a pre-existing rollback-parent destination is rejected. Remove all root/destination reuse paths for this promotion transaction; add focused tests for occupied direct 0305 -> fresh separate destination, candidate/rollback path+device+inode separation, and pre-existing/symlink rollback destination rejection with no materialization side effects. Also amend/resubmit with a <=72-char commit subject: required Commit trailers CI is red solely for 85-char subject. Re-run focused/full promotion suite and git diff --check, update the committed evidence manifest, then request a new exact-head review.

## Summary
Repair the next narrow rollback-materialization collision discovered after PR #4726: keep the existing different-root 0305 runtime untouched and provide a deterministic, independently verified fresh rollback identity for the governed promotion transaction.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
